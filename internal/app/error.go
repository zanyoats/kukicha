package app

import (
	"fmt"
)

type AppError struct {
	Msg string
	Err error
}

func GiveUp(err error) *AppError {
	return &AppError{
		Msg: "I gave up because of the following error.",
		Err: err,
	}
}

func HeresWhy(msg string) *AppError {
	return &AppError{
		Msg: msg,
	}
}

func HeresWhyErr(msg string, err error) *AppError {
	return &AppError{
		Msg: msg,
		Err: err,
	}
}

func (self *AppError) Error() string {
	if self.Err == nil {
		return self.Msg
	}

	return fmt.Sprintf("%s\n%v", self.Msg, self.Err)
}

func (self *AppError) Unwrap() error {
	return self.Err
}
