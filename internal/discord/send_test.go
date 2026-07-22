package discord

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSenderSend(t *testing.T) {
	t.Parallel()
	requests := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if r.Method != http.MethodPost {
			t.Fatalf("method = %s", r.Method)
		}
		var payload WebhookPayload
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatalf("decode payload: %v", err)
		}
		if payload.Embeds[0].Title != "hello" {
			t.Fatalf("title = %q", payload.Embeds[0].Title)
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	urls := []string{server.URL, server.URL}
	err := NewSender(server.Client()).Send(context.Background(), urls, &WebhookPayload{Embeds: []Embed{{Title: "hello"}}})
	if err != nil {
		t.Fatalf("Send() error = %v", err)
	}
	if requests != 2 {
		t.Fatalf("requests = %d, want 2", requests)
	}
}

func TestSenderSendStatusError(t *testing.T) {
	t.Parallel()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusBadGateway)
	}))
	defer server.Close()
	err := NewSender(server.Client()).Send(context.Background(), []string{server.URL}, &WebhookPayload{Embeds: []Embed{{Title: "hello"}}})
	if err == nil {
		t.Fatal("expected error")
	}
}

func TestSenderSendContinuesAfterFailure(t *testing.T) {
	t.Parallel()
	requests := 0
	failing := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusBadGateway)
	}))
	defer failing.Close()
	success := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		var payload WebhookPayload
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatalf("decode payload: %v", err)
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer success.Close()
	err := NewSender(success.Client()).Send(context.Background(), []string{failing.URL, success.URL}, &WebhookPayload{Embeds: []Embed{{Title: "hello"}}})
	if err == nil {
		t.Fatal("expected error")
	}
	if requests != 1 {
		t.Fatalf("requests = %d, want 1", requests)
	}
}

func TestNewSenderUsesDefaultClient(t *testing.T) {
	t.Parallel()
	sender := NewSender(nil)
	if sender.client != http.DefaultClient {
		t.Fatal("expected default client")
	}
}

func TestSenderSendBuildRequestError(t *testing.T) {
	t.Parallel()
	url := "https://token-secret@example.com/%zz"
	err := NewSender(nil).Send(context.Background(), []string{url}, &WebhookPayload{Embeds: []Embed{{Title: "hello"}}})
	if err == nil || err.Error() != "build webhook request: invalid webhook URL" {
		t.Fatal("expected error")
	}
	if strings.Contains(err.Error(), url) || strings.Contains(err.Error(), "token-secret") {
		t.Fatalf("error leaked webhook URL: %v", err)
	}
}

func TestSenderSendMarshalError(t *testing.T) {
	oldMarshal := marshalWebhookPayload
	t.Cleanup(func() { marshalWebhookPayload = oldMarshal })
	marshalWebhookPayload = func(any) ([]byte, error) { return nil, errors.New("marshal failed") }
	err := NewSender(nil).Send(context.Background(), []string{"https://example.test"}, &WebhookPayload{Embeds: []Embed{{Title: "hello"}}})
	if err == nil || err.Error() != "marshal webhook payload: marshal failed" {
		t.Fatalf("Send() error = %v", err)
	}
}

func TestSenderSendPostError(t *testing.T) {
	t.Parallel()
	client := &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
		return nil, &net.OpError{Op: "dial", Err: errors.New("network down")}
	})}
	url := "https://token-secret@example.test/api/webhooks/123"
	err := NewSender(client).Send(context.Background(), []string{url}, &WebhookPayload{Embeds: []Embed{{Title: "hello"}}})
	if err == nil || err.Error() != "post webhook: request failed" {
		t.Fatalf("Send() error = %v", err)
	}
	if strings.Contains(err.Error(), url) || strings.Contains(err.Error(), "token-secret") {
		t.Fatalf("error leaked webhook URL: %v", err)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) {
	return f(r)
}
