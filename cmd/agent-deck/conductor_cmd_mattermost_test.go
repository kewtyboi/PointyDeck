package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/BurntSushi/toml"
	"github.com/asheshgoplani/agent-deck/internal/session"
)

// ---------------------------------------------------------------------------
// MattermostSettings TOML parsing
// ---------------------------------------------------------------------------

// mattermostOnlyConfig mirrors the subset of UserConfig used for these tests.
type mattermostOnlyConfig struct {
	Conductor struct {
		Mattermost session.MattermostSettings `toml:"mattermost"`
	} `toml:"conductor"`
}

func TestMattermostSettings_TOMLParse(t *testing.T) {
	tests := []struct {
		name               string
		toml               string
		wantURL            string
		wantToken          string
		wantTeam           string
		wantChannelID      string
		wantAllowedUsers   []string
		wantAllowAllForDev bool
	}{
		{
			name: "full config with allow_all_users_for_dev true",
			toml: `
[conductor.mattermost]
url               = "http://localhost:8065"
token             = "tok_abc123"
team              = "myteam"
channel_id        = "ch_xyz"
allowed_user_ids  = ["U_ALICE", "U_BOB"]
allow_all_users_for_dev = true
`,
			wantURL:            "http://localhost:8065",
			wantToken:          "tok_abc123",
			wantTeam:           "myteam",
			wantChannelID:      "ch_xyz",
			wantAllowedUsers:   []string{"U_ALICE", "U_BOB"},
			wantAllowAllForDev: true,
		},
		{
			name: "allow_all_users_for_dev false (explicit)",
			toml: `
[conductor.mattermost]
url             = "http://mm.example.com"
token           = "tok_def456"
channel_id      = "ch_abc"
allow_all_users_for_dev = false
`,
			wantURL:            "http://mm.example.com",
			wantToken:          "tok_def456",
			wantChannelID:      "ch_abc",
			wantAllowAllForDev: false,
		},
		{
			name: "allow_all_users_for_dev absent (default false)",
			toml: `
[conductor.mattermost]
url       = "https://mm.internal"
token     = "tok_ghi789"
channel_id = "ch_prod"
allowed_user_ids = ["U_PROD"]
`,
			wantURL:            "https://mm.internal",
			wantToken:          "tok_ghi789",
			wantChannelID:      "ch_prod",
			wantAllowedUsers:   []string{"U_PROD"},
			wantAllowAllForDev: false, // omitted key must default to false (fail closed)
		},
		{
			name: "empty mattermost section",
			toml: `
[conductor.mattermost]
`,
			wantURL:            "",
			wantAllowAllForDev: false,
		},
		{
			name:               "no mattermost section at all",
			toml:               `[conductor]` + "\n",
			wantURL:            "",
			wantAllowAllForDev: false,
		},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			var cfg mattermostOnlyConfig
			if _, err := toml.Decode(tc.toml, &cfg); err != nil {
				t.Fatalf("TOML decode error: %v", err)
			}
			mm := cfg.Conductor.Mattermost

			if mm.URL != tc.wantURL {
				t.Errorf("URL: got %q, want %q", mm.URL, tc.wantURL)
			}
			if tc.wantToken != "" && mm.Token != tc.wantToken {
				t.Errorf("Token: got %q, want %q", mm.Token, tc.wantToken)
			}
			if tc.wantTeam != "" && mm.Team != tc.wantTeam {
				t.Errorf("Team: got %q, want %q", mm.Team, tc.wantTeam)
			}
			if tc.wantChannelID != "" && mm.ChannelID != tc.wantChannelID {
				t.Errorf("ChannelID: got %q, want %q", mm.ChannelID, tc.wantChannelID)
			}
			if len(tc.wantAllowedUsers) > 0 {
				if len(mm.AllowedUserIDs) != len(tc.wantAllowedUsers) {
					t.Errorf("AllowedUserIDs len: got %d, want %d", len(mm.AllowedUserIDs), len(tc.wantAllowedUsers))
				} else {
					for i, u := range tc.wantAllowedUsers {
						if mm.AllowedUserIDs[i] != u {
							t.Errorf("AllowedUserIDs[%d]: got %q, want %q", i, mm.AllowedUserIDs[i], u)
						}
					}
				}
			}
			if mm.AllowAllUsersForDev != tc.wantAllowAllForDev {
				t.Errorf("AllowAllUsersForDev: got %v, want %v", mm.AllowAllUsersForDev, tc.wantAllowAllForDev)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// conductorStatus Mattermost JSON fields
// ---------------------------------------------------------------------------

// mmStatusFields mirrors only the Mattermost fields added to conductorStatus.
// conductorStatus is a function-local type so we test the JSON contract by
// round-tripping an equivalent struct with identical json tags.
type mmStatusFields struct {
	MattermostConfigured bool   `json:"mattermost_configured"`
	MattermostWebsocket  string `json:"mattermost_websocket,omitempty"`
	MattermostLastAuth   string `json:"mattermost_last_auth,omitempty"`
	MattermostLastEvent  string `json:"mattermost_last_event,omitempty"`
	MattermostLastError  string `json:"mattermost_last_error,omitempty"`
}

func TestConductorStatusMattermostFields_JSONMarshal(t *testing.T) {
	tests := []struct {
		name            string
		input           mmStatusFields
		wantContains    []string
		wantNotContains []string
	}{
		{
			name: "configured with all fields populated",
			input: mmStatusFields{
				MattermostConfigured: true,
				MattermostWebsocket:  "connected",
				MattermostLastAuth:   "2026-06-27T10:00:00Z",
				MattermostLastEvent:  "2026-06-27T10:01:00Z",
				MattermostLastError:  "",
			},
			wantContains: []string{
				`"mattermost_configured":true`,
				`"mattermost_websocket":"connected"`,
				`"mattermost_last_auth":"2026-06-27T10:00:00Z"`,
				`"mattermost_last_event":"2026-06-27T10:01:00Z"`,
			},
			// empty string with omitempty must be absent
			wantNotContains: []string{`"mattermost_last_error"`},
		},
		{
			name: "not configured - all omitempty fields absent",
			input: mmStatusFields{
				MattermostConfigured: false,
			},
			wantContains: []string{`"mattermost_configured":false`},
			wantNotContains: []string{
				`"mattermost_websocket"`,
				`"mattermost_last_auth"`,
				`"mattermost_last_event"`,
				`"mattermost_last_error"`,
			},
		},
		{
			name: "error field populated",
			input: mmStatusFields{
				MattermostConfigured: true,
				MattermostWebsocket:  "disconnected",
				MattermostLastError:  "dial tcp: connection refused",
			},
			wantContains: []string{
				`"mattermost_last_error":"dial tcp: connection refused"`,
				`"mattermost_websocket":"disconnected"`,
			},
			wantNotContains: []string{
				`"mattermost_last_auth"`,
				`"mattermost_last_event"`,
			},
		},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			b, err := json.Marshal(tc.input)
			if err != nil {
				t.Fatalf("json.Marshal error: %v", err)
			}
			got := string(b)
			for _, want := range tc.wantContains {
				if !mmContainsStr(got, want) {
					t.Errorf("marshaled JSON missing %q\ngot: %s", want, got)
				}
			}
			for _, notWant := range tc.wantNotContains {
				if mmContainsStr(got, notWant) {
					t.Errorf("marshaled JSON unexpectedly contains %q\ngot: %s", notWant, got)
				}
			}
		})
	}
}

func TestConductorStatusMattermostFields_JSONUnmarshal(t *testing.T) {
	raw := `{
		"mattermost_configured": true,
		"mattermost_websocket": "connected",
		"mattermost_last_auth": "2026-06-27T09:00:00Z",
		"mattermost_last_event": "2026-06-27T09:05:00Z",
		"mattermost_last_error": ""
	}`
	var got mmStatusFields
	if err := json.Unmarshal([]byte(raw), &got); err != nil {
		t.Fatalf("json.Unmarshal error: %v", err)
	}
	if !got.MattermostConfigured {
		t.Error("MattermostConfigured: want true, got false")
	}
	if got.MattermostWebsocket != "connected" {
		t.Errorf("MattermostWebsocket: got %q, want %q", got.MattermostWebsocket, "connected")
	}
	if got.MattermostLastAuth != "2026-06-27T09:00:00Z" {
		t.Errorf("MattermostLastAuth: got %q", got.MattermostLastAuth)
	}
	if got.MattermostLastEvent != "2026-06-27T09:05:00Z" {
		t.Errorf("MattermostLastEvent: got %q", got.MattermostLastEvent)
	}
}

// ---------------------------------------------------------------------------
// Status JSON file read + field population (mirrors handleConductorStatus logic)
// ---------------------------------------------------------------------------

// mmStatusFileReader replicates the snippet in handleConductorStatus that reads
// the mattermost-status.json file and populates conductorStatus fields.
// Testing it as a standalone helper avoids needing conductor metadata.
func mmStatusFileReader(statusPath string) mmStatusFields {
	var out mmStatusFields
	data, err := os.ReadFile(statusPath) //nolint:gosec // test helper, path from test
	if err != nil {
		return out
	}
	var snap map[string]interface{}
	if json.Unmarshal(data, &snap) != nil {
		return out
	}
	if v, ok := snap["websocket"].(string); ok {
		out.MattermostWebsocket = v
	}
	if v, ok := snap["last_successful_auth"].(string); ok {
		out.MattermostLastAuth = v
	}
	if v, ok := snap["last_event_time"].(string); ok {
		out.MattermostLastEvent = v
	}
	if v, ok := snap["last_error"].(string); ok {
		out.MattermostLastError = v
	}
	return out
}

func TestMattermostStatusFileReader(t *testing.T) {
	t.Run("reads all fields from valid status file", func(t *testing.T) {
		dir := t.TempDir()
		statusPath := filepath.Join(dir, "mattermost-status.json")
		content := `{
			"websocket": "connected",
			"connected": true,
			"last_successful_auth": "2026-06-27T08:00:00Z",
			"last_event_time": "2026-06-27T08:30:00Z",
			"last_error": ""
		}`
		if err := os.WriteFile(statusPath, []byte(content), 0644); err != nil {
			t.Fatalf("write status file: %v", err)
		}
		got := mmStatusFileReader(statusPath)
		if got.MattermostWebsocket != "connected" {
			t.Errorf("websocket: got %q, want %q", got.MattermostWebsocket, "connected")
		}
		if got.MattermostLastAuth != "2026-06-27T08:00:00Z" {
			t.Errorf("last_successful_auth: got %q", got.MattermostLastAuth)
		}
		if got.MattermostLastEvent != "2026-06-27T08:30:00Z" {
			t.Errorf("last_event_time: got %q", got.MattermostLastEvent)
		}
		// empty string last_error must not panic and must be empty
		if got.MattermostLastError != "" {
			t.Errorf("last_error: got %q, want empty", got.MattermostLastError)
		}
	})

	t.Run("missing file returns zero value without panic", func(t *testing.T) {
		got := mmStatusFileReader("/nonexistent/path/mattermost-status.json")
		if got.MattermostWebsocket != "" || got.MattermostLastAuth != "" {
			t.Errorf("expected zero value for missing file, got %+v", got)
		}
	})

	t.Run("invalid JSON returns zero value without panic", func(t *testing.T) {
		dir := t.TempDir()
		statusPath := filepath.Join(dir, "mattermost-status.json")
		if err := os.WriteFile(statusPath, []byte("not json {{{"), 0644); err != nil {
			t.Fatalf("write status file: %v", err)
		}
		got := mmStatusFileReader(statusPath)
		if got.MattermostWebsocket != "" {
			t.Errorf("expected empty websocket on bad JSON, got %q", got.MattermostWebsocket)
		}
	})

	t.Run("disconnected state with error message", func(t *testing.T) {
		dir := t.TempDir()
		statusPath := filepath.Join(dir, "mattermost-status.json")
		content := `{
			"websocket": "disconnected",
			"connected": false,
			"last_successful_auth": "",
			"last_event_time": "",
			"last_error": "dial tcp: connection refused"
		}`
		if err := os.WriteFile(statusPath, []byte(content), 0644); err != nil {
			t.Fatalf("write status file: %v", err)
		}
		got := mmStatusFileReader(statusPath)
		if got.MattermostWebsocket != "disconnected" {
			t.Errorf("websocket: got %q, want disconnected", got.MattermostWebsocket)
		}
		if got.MattermostLastError != "dial tcp: connection refused" {
			t.Errorf("last_error: got %q", got.MattermostLastError)
		}
	})
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

func mmContainsStr(s, sub string) bool {
	return len(s) >= len(sub) && (s == sub || len(sub) == 0 ||
		func() bool {
			for i := 0; i <= len(s)-len(sub); i++ {
				if s[i:i+len(sub)] == sub {
					return true
				}
			}
			return false
		}())
}
