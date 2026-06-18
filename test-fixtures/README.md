# Test fixtures

These are real QR codes for testing the v2 scanner end-to-end.

| File | Encodes | Use |
|---|---|---|
| `nutella-qr.png` | OFF URL for Nutella (`https://world.openfoodfacts.org/product/3017620422003`) | Tests URL extraction + OFF fallback + high-category avoid flow |
| `cocacola-qr.png` | OFF URL for Coca-Cola (`https://world.openfoodfacts.org/product/5449000000996`) | Tests another OFF fallback path |
| `barcode-plain.png` | Raw EAN-13 barcode `3017620422003` | Tests the simple "raw number" decode path |

## How to test the scanner

1. Push these to your phone (AirDrop, email, etc.) and display one
2. Open NickelTrack on your phone, tap "📷 Scan"
3. Point the camera at the QR
4. Verify the result card shows the food with the right category + AVOID flag (for high)

## Regenerating

```python
import qrcode
qrcode.make("https://world.openfoodfacts.org/product/3017620422003").save("nutella-qr.png")
```
