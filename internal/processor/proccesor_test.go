package processor

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

type decodeTestJob struct{}

func (*decodeTestJob) Run(context.Context) error { return nil }

func TestDecode(t *testing.T) {
	type input struct {
		Path string `json:"path"`
	}

	tests := []struct {
		name    string
		payload string
		want    input
		wantErr string
	}{
		{
			name:    "valid payload",
			payload: `{"path":"/music"}`,
			want:    input{Path: "/music"},
		},
		{
			name:    "surrounding whitespace",
			payload: " \n\t{\"path\":\"/music\"}\r\n ",
			want:    input{Path: "/music"},
		},
		{
			name:    "omitted field",
			payload: `{}`,
			want:    input{},
		},
		{
			name:    "empty payload",
			payload: "",
			wantErr: "decode job payload:",
		},
		{
			name:    "whitespace only",
			payload: " \n\t ",
			wantErr: "decode job payload:",
		},
		{
			name:    "null",
			payload: `null`,
			wantErr: "job payload must not be null",
		},
		{
			name:    "null with whitespace",
			payload: " \nnull\t ",
			wantErr: "job payload must not be null",
		},
		{
			name:    "malformed JSON",
			payload: `{"path":`,
			wantErr: "decode job payload:",
		},
		{
			name:    "unknown field",
			payload: `{"path":"/music","extra":true}`,
			wantErr: `json: unknown field "extra"`,
		},
		{
			name:    "wrong field type",
			payload: `{"path":42}`,
			wantErr: "decode job payload:",
		},
		{
			name:    "wrong top-level type",
			payload: `[]`,
			wantErr: "decode job payload:",
		},
		{
			name:    "trailing object",
			payload: `{"path":"/music"} {}`,
			wantErr: "job payload contains trailing JSON",
		},
		{
			name:    "trailing null",
			payload: `{"path":"/music"} null`,
			wantErr: "job payload contains trailing JSON",
		},
		{
			name:    "invalid trailing data",
			payload: `{"path":"/music"} garbage`,
			wantErr: "decode trailing job payload:",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			wantJob := &decodeTestJob{}
			var gotInput input
			calls := 0

			factory := Decode(func(value input) (Job, error) {
				calls++
				gotInput = value
				return wantJob, nil
			})

			got, err := factory(json.RawMessage(tt.payload))

			if tt.wantErr != "" {
				if err == nil || !strings.Contains(err.Error(), tt.wantErr) {
					t.Fatalf("error = %v, want containing %q", err, tt.wantErr)
				}
				if got != nil {
					t.Errorf("job = %v, want nil", got)
				}
				if calls != 0 {
					t.Errorf("builder called %d times, want 0", calls)
				}
				return
			}

			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if calls != 1 {
				t.Errorf("builder called %d times, want 1", calls)
			}
			if gotInput != tt.want {
				t.Errorf("builder input = %+v, want %+v", gotInput, tt.want)
			}
			if got != wantJob {
				t.Errorf("job = %v, want builder's job %v", got, wantJob)
			}
		})
	}
}
