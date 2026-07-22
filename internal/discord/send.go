// Package discord builds and sends Discord webhook payloads.
package discord

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
)

var marshalWebhookPayload = json.Marshal

// Sender posts Discord webhook payloads.
type Sender struct {
	client *http.Client
}

// NewSender builds a webhook sender. When client is nil, http.DefaultClient is used.
func NewSender(client *http.Client) *Sender {
	if client == nil {
		client = http.DefaultClient
	}
	return &Sender{client: client}
}

// Send posts one payload to each webhook URL and requires a successful HTTP status.
func (s *Sender) Send(ctx context.Context, webhookURLs []string, payload *WebhookPayload) error {
	body, err := marshalWebhookPayload(payload)
	if err != nil {
		return fmt.Errorf("marshal webhook payload: %w", err)
	}
	var errs []error
	for _, webhookURL := range webhookURLs {
		if err := s.sendOne(ctx, webhookURL, body); err != nil {
			errs = append(errs, err)
		}
	}
	return errors.Join(errs...)
}

func (s *Sender) sendOne(ctx context.Context, webhookURL string, body []byte) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, webhookURL, bytes.NewReader(body))
	if err != nil {
		return errors.New("build webhook request: invalid webhook URL")
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := s.client.Do(req)
	if err != nil {
		return errors.New("post webhook: request failed")
	}
	defer func() {
		_ = resp.Body.Close()
	}()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("post webhook: unexpected status %s", resp.Status)
	}
	return nil
}
