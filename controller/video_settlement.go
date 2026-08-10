package controller

import (
	"errors"
	"net/http"
	"strconv"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

func ApplyVideoSettlement(c *gin.Context) {
	var evidence service.VideoSettlementEvidence
	if err := c.ShouldBindJSON(&evidence); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"success": false, "message": "invalid settlement request"})
		return
	}
	outcome, err := service.ApplyVideoTaskSettlement(c.Request.Context(), evidence)
	if err != nil {
		status := http.StatusInternalServerError
		switch {
		case errors.Is(err, service.ErrVideoSettlementInvalid):
			status = http.StatusBadRequest
		case errors.Is(err, service.ErrVideoSettlementConflict):
			status = http.StatusConflict
		case errors.Is(err, service.ErrVideoSettlementTaskNotReady), errors.Is(err, gorm.ErrRecordNotFound):
			status = http.StatusNotFound
		case errors.Is(err, service.ErrVideoSettlementPaymentRequired):
			status = http.StatusPaymentRequired
		}
		c.JSON(status, gin.H{"success": false, "message": err.Error()})
		return
	}
	common.ApiSuccess(c, outcome)
}

func GetPendingVideoSettlements(c *gin.Context) {
	limit, _ := strconv.Atoi(c.DefaultQuery("limit", "1000"))
	items, err := service.ListPendingVideoSettlementTasks(limit)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"success": false, "message": "failed to load pending settlements"})
		return
	}
	common.ApiSuccess(c, items)
}
