package web

func yesNo(value *bool) string {
	if value != nil && *value {
		return "Yes"
	}

	return "No"
}
