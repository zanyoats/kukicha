package command

import (
	"crypto/rand"
	"errors"
	"math"
	"math/big"
	"math/bits"
)

// Parameters for the algorithm: `affine permutation modulo a prime`,
// combined with cycle walking.
// The formula is `f(x) = (a*x+b)` mod p where
// p is the prime, a is the multiplier (or step), and b is the offset.
// Cycle walking restricts the permutation to [0, N-1] by repeatedly applying
// `f` until the output is valid. This has the nice property of being random
// access too -- though we don't use this feature as we sequentially traverse
// the permutation.
type radioShuffle struct {
	count, prime, multiplier, offset int64
}

func radioPrime(count int64) (int64, error) {
	if count < 1 || count == math.MaxInt64 {
		return 0, errors.New("unsupported radio track count")
	}
	p := count + 1
	for !big.NewInt(p).ProbablyPrime(0) {
		if p == math.MaxInt64 {
			return 0, errors.New("radio prime exceeds integer range")
		}
		p++
	}
	return p, nil
}

func newRadioShuffle(count int64) (radioShuffle, error) {
	p, err := radioPrime(count)
	if err != nil {
		return radioShuffle{}, err
	}
	a, err := rand.Int(rand.Reader, big.NewInt(p-1))
	if err != nil {
		return radioShuffle{}, err
	}
	b, err := rand.Int(rand.Reader, big.NewInt(p))
	if err != nil {
		return radioShuffle{}, err
	}
	return radioShuffle{count, p, a.Int64() + 1, b.Int64()}, nil
}

// Mul64/Div64 avoid overflowing the intermediate product.
// The formula `((a*x mod p) + b)` mod p is equal to `(a*x + b) mod p`. The later
// formula is computed to avoid overflowing.
func (s radioShuffle) index(position int64) int64 {
	x := uint64(position)
	p := uint64(s.prime)
	for { /* cycle walking guarentees termination */
		hi, lo := bits.Mul64(uint64(s.multiplier), x)
		_, remainder := bits.Div64(hi, lo, p)
		x = (remainder + uint64(s.offset)) % p
		if x < uint64(s.count) { /* cycle walking condition */
			return int64(x)
		}
	}
}
