package billingexpr

import "testing"

func TestImage25VerifiedResolutionPrices(t *testing.T) {
	expr := `param("size") == "4096x4096" ? tier("4k", 1000000) : param("size") == "2048x2048" ? tier("2k", 500000) : tier("1k", 250000)`
	for _, tc := range []struct {
		body  string
		quota int
	}{
		{`{"size":"1024x1024","n":1}`, 18750},
		{`{"size":"2048x2048","n":1}`, 37500},
		{`{"size":"4096x4096","n":1}`, 75000},
	} {
		snap := &BillingSnapshot{ExprString: expr, QuotaPerUnit: 500000, GroupRatio: 0.15}
		got, err := ComputeTieredQuotaWithRequest(snap, TokenParams{}, RequestInput{Body: []byte(tc.body)})
		if err != nil {
			t.Fatal(err)
		}
		if got.ActualQuotaAfterGroup != tc.quota {
			t.Fatalf("%s: got %d want %d", tc.body, got.ActualQuotaAfterGroup, tc.quota)
		}
	}
}
