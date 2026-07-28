package common

import (
	cryptorand "crypto/rand"
	"errors"
	"fmt"
	"io"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
)

type verificationValue struct {
	code string
	time time.Time
}

const (
	EmailVerificationPurpose = "v"
	PasswordResetPurpose     = "r"
)

var verificationMutex sync.Mutex
var verificationMap map[string]verificationValue
var verificationMapMaxSize = 10
var VerificationValidMinutes = 10

func GenerateVerificationCode(length int) string {
	code := uuid.New().String()
	code = strings.Replace(code, "-", "", -1)
	if length == 0 {
		return code
	}
	return code[:length]
}

// GenerateNumericVerificationCode creates a fixed-width decimal code suitable
// for user-facing one-time verification inputs. Rejection sampling avoids the
// modulo bias that would otherwise make some digits more likely than others.
func GenerateNumericVerificationCode(length int) (string, error) {
	return generateNumericVerificationCode(cryptorand.Reader, length)
}

func generateNumericVerificationCode(reader io.Reader, length int) (string, error) {
	if length <= 0 {
		return "", errors.New("verification code length must be positive")
	}

	code := make([]byte, length)
	candidate := make([]byte, 1)
	for index := 0; index < length; {
		if _, err := io.ReadFull(reader, candidate); err != nil {
			return "", fmt.Errorf("generate numeric verification code: %w", err)
		}
		if candidate[0] >= 250 {
			continue
		}
		code[index] = '0' + candidate[0]%10
		index++
	}
	return string(code), nil
}

func RegisterVerificationCodeWithKey(key string, code string, purpose string) {
	verificationMutex.Lock()
	defer verificationMutex.Unlock()
	verificationMap[purpose+key] = verificationValue{
		code: code,
		time: time.Now(),
	}
	if len(verificationMap) > verificationMapMaxSize {
		removeExpiredPairs()
	}
}

func VerifyCodeWithKey(key string, code string, purpose string) bool {
	verificationMutex.Lock()
	defer verificationMutex.Unlock()
	value, okay := verificationMap[purpose+key]
	now := time.Now()
	if !okay || int(now.Sub(value.time).Seconds()) >= VerificationValidMinutes*60 {
		return false
	}
	return code == value.code
}

func DeleteKey(key string, purpose string) {
	verificationMutex.Lock()
	defer verificationMutex.Unlock()
	delete(verificationMap, purpose+key)
}

// no lock inside, so the caller must lock the verificationMap before calling!
func removeExpiredPairs() {
	now := time.Now()
	for key := range verificationMap {
		if int(now.Sub(verificationMap[key].time).Seconds()) >= VerificationValidMinutes*60 {
			delete(verificationMap, key)
		}
	}
}

func init() {
	verificationMutex.Lock()
	defer verificationMutex.Unlock()
	verificationMap = make(map[string]verificationValue)
}
