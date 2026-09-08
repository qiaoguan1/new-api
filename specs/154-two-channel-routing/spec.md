# Production two-channel routing policy

## Objective

Keep at most two enabled upstream channels for each public core model while preserving video and Topaz capabilities. Selection is based on verified balance, real cost evidence, recent success rate, and latency.

## Rules

- Text models use Haina as the primary route and Hanhe as the second route.
- GPT-Image-2 uses Haina as the primary route and Maolao as the second route.
- Banana remains on Paisio because no second verified provider is currently available.
- Video and Topaz routes remain unchanged.
- Disabled providers remain configured for recoverable reactivation; they are not deleted.
- Text sell prices use the highest trusted retained cost multiplied by 1.5.
- When Haina actual-cost collection is incomplete, text and image prices may remain unchanged or increase but must not automatically decrease.

## Acceptance criteria

- Every listed core model has no more than two enabled abilities.
- Empty-balance and high-error providers are excluded from production selection.
- End-to-end text and image canaries succeed through the selected routes.
- Daily audit and patrol remain healthy after the change.
