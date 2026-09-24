package common

import "os"

// IsQuotaDBAuthoritative disables delayed/cached money when native and async APIs share wallets.
// It must be enabled on every serving native instance before public video reservations start.
func IsQuotaDBAuthoritative() bool { return os.Getenv("QUOTA_DB_AUTHORITATIVE") == "true" }
