package constant

const (
	XingTuVideoContractLegacy  = "xtai-video-billing-v2"
	XingTuVideoContractCurrent = "xtai-video-billing-v2.1"
)

// IsXingTuVideoContract reports whether version belongs to the supported
// migration window. New submissions use XingTuVideoContractCurrent; legacy is
// retained only so already-created tasks can finish safely.
func IsXingTuVideoContract(version string) bool {
	return version == XingTuVideoContractCurrent || version == XingTuVideoContractLegacy
}
