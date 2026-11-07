package buildinfo

// The release workflow sets version to the selected Git tag at link time.
var version = "dev"

// Version returns the release tag, or "dev" for local builds.
func Version() string {
	return version
}
