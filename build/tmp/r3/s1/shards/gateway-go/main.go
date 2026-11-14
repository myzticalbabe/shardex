// gateway-go — OpenAI-compatible front end over llama-server backends.
// Stdlib only. Routes by `model` substring, round-robins otherwise,
// fails over on errors, relays SSE streaming. Health-probed every 15s.
//
// Usage: gateway-go -listen 127.0.0.1:18081 -upstream http://127.0.0.1:18090 -upstream http://127.0.0.1:18091
package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type upstream struct {
	url     string
	model   string
	healthy atomic.Bool
	mu      sync.Mutex
}

type state struct {
	ups    []*upstream
	rr     atomic.Uint64
	client *http.Client
}

type multiFlag []string

func (m *multiFlag) String() string     { return strings.Join(*m, ",") }
func (m *multiFlag) Set(v string) error { *m = append(*m, strings.TrimRight(v, "/")); return nil }

func (s *state) probe() {
	for _, u := range s.ups {
		func() {
			c, err := s.client.Get(u.url + "/health")
			ok := err == nil && c != nil && c.StatusCode == 200
			if c != nil {
				io.Copy(io.Discard, c.Body)
				c.Body.Close()
			}
			u.healthy.Store(ok)
			if !ok {
				return
			}
			c2, err := s.client.Get(u.url + "/v1/models")
			if err != nil || c2.StatusCode != 200 {
				if c2 != nil {
					c2.Body.Close()
				}
				return
			}
			var v struct {
				Data []struct {
					ID string `json:"id"`
				} `json:"data"`
			}
			if json.NewDecoder(c2.Body).Decode(&v) == nil && len(v.Data) > 0 && v.Data[0].ID != "" {
				c2.Body.Close()
				u.mu.Lock()
				u.model = v.Data[0].ID
				u.mu.Unlock()
				return
			}
			c2.Body.Close()
		}()
	}
}

func (s *state) pick(want string) *upstream {
	w := strings.ToLower(want)
	if w != "" {
		for _, u := range s.ups {
			u.mu.Lock()
			m := u.model
			u.mu.Unlock()
			if u.healthy.Load() && strings.Contains(strings.ToLower(m), w) {
				return u
			}
		}
	}
	var healthy []*upstream
	for _, u := range s.ups {
		if u.healthy.Load() {
			healthy = append(healthy, u)
		}
	}
	if len(healthy) == 0 {
		if len(s.ups) == 0 {
			return nil
		}
		return s.ups[0]
	}
	return healthy[s.rr.Add(1)%uint64(len(healthy))]
}

func (s *state) forward(path string, w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "bad body", 400)
		return
	}
	var v map[string]any
	want := ""
	if json.Unmarshal(body, &v) == nil {
		want, _ = v["model"].(string)
	}
	ct := r.Header.Get("Content-Type")
	if ct == "" {
		ct = "application/json"
	}
	tried := map[*upstream]bool{}
	for range s.ups {
		u := s.pick(want)
		if u == nil || tried[u] {
			continue
		}
		tried[u] = true
		t0 := time.Now()
		req, _ := http.NewRequest("POST", u.url+path, bytes.NewReader(body))
		req.Header.Set("Content-Type", ct)
		resp, err := s.client.Do(req)
		if err != nil {
			fmt.Printf("POST %s -> %s unreachable (%v), trying next\n", path, u.url, err)
			u.healthy.Store(false)
			continue
		}
		if resp.StatusCode >= 500 {
			fmt.Printf("POST %s -> %s error %d, trying next\n", path, u.url, resp.StatusCode)
			resp.Body.Close()
			continue
		}
		fmt.Printf("POST %s -> %s %d (%dms)\n", path, u.url, resp.StatusCode, time.Since(t0).Milliseconds())
		w.Header().Set("Content-Type", resp.Header.Get("Content-Type"))
		w.WriteHeader(resp.StatusCode)
		fl, _ := w.(http.Flusher)
		buf := make([]byte, 32*1024)
		for {
			n, re := resp.Body.Read(buf)
			if n > 0 {
				w.Write(buf[:n])
				if fl != nil {
					fl.Flush()
				}
			}
			if re != nil {
				break
			}
		}
		resp.Body.Close()
		return
	}
	w.WriteHeader(502)
	json.NewEncoder(w).Encode(map[string]string{"error": "no healthy upstream"})
}

func main() {
	var listen string
	var ups multiFlag
	flag.StringVar(&listen, "listen", "127.0.0.1:18081", "listen address")
	flag.Var(&ups, "upstream", "upstream base URL (repeatable)")
	flag.Parse()
	if len(ups) == 0 {
		ups = multiFlag{"http://127.0.0.1:18090", "http://127.0.0.1:18091"}
	}
	st := &state{client: &http.Client{Timeout: 10 * time.Minute}}
	for _, u := range ups {
		st.ups = append(st.ups, &upstream{url: u})
	}
	st.probe()
	go func() {
		for range time.Tick(15 * time.Second) {
			st.probe()
		}
	}()
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		type uo struct {
			URL     string `json:"url"`
			Model   string `json:"model"`
			Healthy bool   `json:"healthy"`
		}
		out := []uo{}
		for _, u := range st.ups {
			u.mu.Lock()
			m := u.model
			u.mu.Unlock()
			out = append(out, uo{u.url, m, u.healthy.Load()})
		}
		json.NewEncoder(w).Encode(map[string]any{"status": "ok", "upstreams": out})
	})
	mux.HandleFunc("/v1/models", func(w http.ResponseWriter, r *http.Request) {
		var data []any
		for _, u := range st.ups {
			if !u.healthy.Load() {
				continue
			}
			c, err := st.client.Get(u.url + "/v1/models")
			if err != nil || c.StatusCode != 200 {
				if c != nil {
					c.Body.Close()
				}
				continue
			}
			var v struct {
				Data []any `json:"data"`
			}
			if json.NewDecoder(c.Body).Decode(&v) == nil {
				data = append(data, v.Data...)
			}
			c.Body.Close()
		}
		if data == nil {
			data = []any{}
		}
		json.NewEncoder(w).Encode(map[string]any{"object": "list", "data": data})
	})
	mux.HandleFunc("/v1/chat/completions", func(w http.ResponseWriter, r *http.Request) {
		st.forward("/v1/chat/completions", w, r)
	})
	mux.HandleFunc("/v1/completions", func(w http.ResponseWriter, r *http.Request) {
		st.forward("/v1/completions", w, r)
	})
	fmt.Printf("gateway-go listening on http://%s\n", listen)
	if err := http.ListenAndServe(listen, mux); err != nil {
		fmt.Println("fatal:", err)
	}
}
