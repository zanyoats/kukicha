package web

import "testing"

func TestLibraryCategoryURLs(t *testing.T) {
	tests := []struct {
		name string
		got  string
		want string
	}{
		{
			name: "artist",
			got:  artistURL("/kukicha/app", 32),
			want: "/kukicha/app/library/artist?artist_id=32",
		},
		{
			name: "genre",
			got:  genreURL("/kukicha/app", 32),
			want: "/kukicha/app/library/genre?genre_id=32",
		},
		{
			name: "style",
			got:  styleURL("/kukicha/app", 32),
			want: "/kukicha/app/library/style?style_id=32",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if test.got != test.want {
				t.Errorf("URL = %q, want %q", test.got, test.want)
			}
		})
	}
}
