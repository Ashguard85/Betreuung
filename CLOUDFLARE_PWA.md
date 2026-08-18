# Cloudflare-Konfiguration für v40

## Ziel

- `betreuung.DEINE-DOMAIN.TLD`: bestehende Flask-App hinter Cloudflare Access
- Browserzugang: OTP / normale Allow-Policy
- PWA-Zugang: Service Auth / Service Token
- `betreuung2.DEINE-DOMAIN.TLD`: statisches GitHub-Pages-Frontend

## Access

Auf der Access Application für `betreuung.DEINE-DOMAIN.TLD` beide Zugangsarten zulassen:

1. normale **Allow**-Policy für deinen OTP-Benutzer
2. **Service Auth**-Policy mit den Service Tokens der iPhones

Am besten pro Gerät ein separates Service Token erstellen.

## CORS / Preflight

Die PWA läuft auf einem anderen Origin. Browser senden deshalb vor Requests mit den Cloudflare-Service-Token-Headern einen anonymen `OPTIONS`-Preflight.

**Empfohlen für iPhone/Home-Screen-PWA:** In der Access Application unter **Advanced settings → Cross-Origin Resource Sharing (CORS)** die Option **Bypass OPTIONS requests to origin** aktivieren. Flask beantwortet den Preflight und prüft den Origin gegen `PWA_ALLOWED_ORIGINS`. Dadurch muss Cloudflare nicht selbst einen einzelnen Browser-Origin nachbilden.

Der Origin darf weiterhin niemals pauschal mit `*` freigegeben werden.

Flask liefert für erlaubte Origins:

```text
Access-Control-Allow-Origin: https://betreuung2.DEINE-DOMAIN.TLD
Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS
Access-Control-Allow-Headers: Content-Type, CF-Access-Client-ID, CF-Access-Client-Secret
Access-Control-Expose-Headers: Content-Disposition, Content-Type
Access-Control-Max-Age: 86400
```

Im Container muss der von der Pages-PWA angezeigte Origin stehen:

```text
PWA_ALLOWED_ORIGINS=https://betreuung2.DEINE-DOMAIN.TLD
```

Mehrere explizite Origins sind kommasepariert möglich, z. B. bei einer älteren bereits installierten PWA:

```text
PWA_ALLOWED_ORIGINS=https://betreuung2.DEINE-DOMAIN.TLD,https://ashguard85.github.io
```

Die bisherige Variable `PWA_ALLOWED_ORIGIN` bleibt weiterhin unterstützt.

## iCal-Abos

`/calendar.ics` bleibt ein Sonderfall: Apple Kalender kann das Cloudflare-Service-Token nicht als Custom Header senden. Falls Kalender-Abos verwendet werden, den bestehenden gezielten Cloudflare-Bypass nur für `/calendar.ics` beibehalten; der lange iCal-Token der App bleibt die Absicherung dieses Endpunkts.

## Apple-Kalender-Direktimport

Der Direktimport nutzt den vorhandenen read-only `/calendar.ics`-Bypass mit `ICAL_TOKEN`. `/export.ics` und `/api/entries/.../ics` bleiben hinter normalem Access/Login; dafür ist kein neuer Bypass nötig.

## Web Push (v60)

Die Geräte-ID wird ohne zusätzlichen Custom-Header als nicht-geheimer API-Queryparameter übertragen. In Cloudflare Access muss der anonyme `OPTIONS`-Preflight für die bestehenden Service-Token-Header weiterhin bis zum Origin durchgelassen werden. Für Web Push ist kein zusätzlicher öffentlich erreichbarer Backend-Endpunkt nötig; der Docker-Server sendet ausgehend direkt an die im Browser registrierten Push-Endpunkte. Der private VAPID-Schlüssel liegt persistent unter `/app/data/webpush-vapid-private.pem` und darf bei einer Servermigration nicht verloren gehen, solange bestehende Push-Subscriptions weiter funktionieren sollen.
