package model

import (
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/setting"
	"github.com/glebarez/sqlite"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestTokenDefaultAutoGroupPersistence(t *testing.T) {
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, db.AutoMigrate(&Token{}))
	previousDB, previousSetting, previousRedis := DB, setting.DefaultUseAutoGroup, common.RedisEnabled
	DB, common.RedisEnabled = db, false
	t.Cleanup(func() {
		DB, setting.DefaultUseAutoGroup, common.RedisEnabled = previousDB, previousSetting, previousRedis
		sqlDB, closeErr := db.DB()
		if closeErr == nil {
			_ = sqlDB.Close()
		}
	})
	for _, tc := range []struct {
		name, group, want string
		enabled           bool
	}{
		{"enabled blank", "", "auto", true},
		{"disabled blank", "", "", false},
		{"explicit default", "default", "default", true},
		{"explicit text", "文", "文", true},
		{"explicit legacy", "SAN", "SAN", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			setting.DefaultUseAutoGroup = tc.enabled
			ip := "192.0.2.10"
			token := Token{UserId: 17, Key: tc.name, Name: tc.name, Group: tc.group,
				Status: 2, RemainQuota: 91234, UsedQuota: 345, ModelLimitsEnabled: true,
				ModelLimits: "gpt-6", AllowIps: &ip, ExpiredTime: 1900000000}
			require.NoError(t, token.Insert())
			var stored Token
			require.NoError(t, db.First(&stored, token.Id).Error)
			assert.Equal(t, tc.want, stored.Group)
			assert.Equal(t, token.Key, stored.Key)
			assert.Equal(t, 2, stored.Status)
			assert.Equal(t, 91234, stored.RemainQuota)
			assert.Equal(t, 345, stored.UsedQuota)
			assert.Equal(t, &ip, stored.AllowIps)
			assert.True(t, stored.ModelLimitsEnabled)
			assert.Equal(t, "gpt-6", stored.ModelLimits)
			stored.Group = tc.group
			stored.ApplyDefaultGroup()
			require.NoError(t, stored.Update())
			var updated Token
			require.NoError(t, db.First(&updated, token.Id).Error)
			assert.Equal(t, tc.want, updated.Group)
			assert.Equal(t, stored.Key, updated.Key)
			assert.Equal(t, stored.RemainQuota, updated.RemainQuota)
			assert.Equal(t, stored.ModelLimits, updated.ModelLimits)
			assert.Equal(t, stored.AllowIps, updated.AllowIps)
			if tc.name == "disabled blank" {
				setting.DefaultUseAutoGroup = true
				updated.Status = 1
				require.NoError(t, updated.Update())
				var statusOnly Token
				require.NoError(t, db.First(&statusOnly, token.Id).Error)
				assert.Equal(t, 1, statusOnly.Status)
				assert.Empty(t, statusOnly.Group)
			}
		})
	}
}
