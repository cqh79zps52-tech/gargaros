# Utiliser Gargaros (bureau caché `agent_dsk`)

Guide rapide pour piloter le poste **en parallèle** de l'utilisateur : l'agent agit sur un bureau
Windows caché, sans bouger la souris ni voler le focus clavier.

## 1. Démarrer le serveur

```powershell
python -m gargaros
```

Au démarrage, Gargaros affiche l'URL et le **bearer token** (aussi écrit dans
`%APPDATA%\Gargaros\token`). Le bureau caché `agent_dsk` est créé automatiquement.

```powershell
$token = Get-Content $env:APPDATA\Gargaros\token
$H = @{ Authorization = "Bearer $token"; Host = "127.0.0.1:7331" }
```

> Toutes les routes (sauf `/health`) exigent l'en-tête `Authorization: Bearer <token>` **et** un
> `Host` valant `127.0.0.1:7331` ou `localhost:7331`.

Désactiver le bureau caché (revenir au pilotage du bureau visible via Windows-MCP) :
`setx GARGAROS_HIDDEN_DESKTOP_ENABLED 0` puis relancer.

## 2. Lancer une application sur le bureau caché

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:7331/agent/launch_app -Headers $H `
  -ContentType application/json -Body '{"cmdline":"notepad.exe"}'
# -> { ok: True, pid: 1234, desktop: "agent_dsk" }
```

`cmdline` est passé à `CreateProcess`. Un nom seul (`notepad.exe`, `chrome.exe`) est résolu via le
PATH et le registre `App Paths`. L'app **n'apparaît jamais à l'écran**.

## 3. Voir ce que fait l'agent

```powershell
# Image JPEG composée des fenêtres cachées
Invoke-WebRequest http://127.0.0.1:7331/screenshot -Headers $H -OutFile shot.jpg

# Chercher du texte à l'écran (OCR) -> bbox + center
Invoke-RestMethod "http://127.0.0.1:7331/find?text=Fichier" -Headers $H
```

Les coordonnées renvoyées sont des **pixels écran absolus** = l'espace de l'image `/screenshot`.

## 4. Agir — chemin recommandé : UIA (zéro curseur)

```powershell
# a) Lister les contrôles interactifs des fenêtres cachées (= labels)
$snap = Invoke-RestMethod -Method Post http://127.0.0.1:7331/ui/snapshot -Headers $H `
  -ContentType application/json -Body '{}'
$snap.elements   # [{ label, name, control_type, bounds, center, window }, ...]

# b) Taper dans un champ (par label)
Invoke-RestMethod -Method Post http://127.0.0.1:7331/ui/type_label -Headers $H `
  -ContentType application/json -Body '{"label":1,"text":"bonjour","press_enter":false}'

# c) Cliquer un bouton / item de menu (par label)
Invoke-RestMethod -Method Post http://127.0.0.1:7331/ui/click_label -Headers $H `
  -ContentType application/json -Body '{"label":5}'
```

> `click_label` / `type_label` renvoient **409** sur un label inconnu : refaire `/ui/snapshot` d'abord
> (les labels changent quand l'UI change). Réponse : `{ ok, method }` (méthode UIA réellement utilisée).

## 5. Agir — repli par coordonnées

Si UIA ne couvre pas un élément (canvas, dessin maison), utilise les coordonnées absolues lues sur
`/screenshot` ou `/find` :

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:7331/click  -Headers $H -ContentType application/json -Body '{"x":900,"y":500}'
Invoke-RestMethod -Method Post http://127.0.0.1:7331/type   -Headers $H -ContentType application/json -Body '{"text":"hello"}'
Invoke-RestMethod -Method Post http://127.0.0.1:7331/scroll -Headers $H -ContentType application/json -Body '{"dy":-3,"x":900,"y":500}'
```

Sur le bureau caché, `/move` en mode `relative:true` renvoie **409** (pas de curseur à déplacer).

## 6. SDK Python

```python
from gargaros.client import Client

with Client() as g:                 # token auto-découvert
    snap = g.ui_snapshot()          # contrôles labellisés des fenêtres cachées
    g.ui_type_label(1, "bonjour")
    g.ui_click_label(5)
    img = g.screenshot()            # bytes JPEG
```

> Le SDK n'a pas encore de helper pour `/agent/launch_app` : lancer une app sur le bureau caché se
> fait par requête HTTP `POST /agent/launch_app {"cmdline": "..."}`.

## 7. Cas du navigateur

Fonctionne, mais sensible :
- Un navigateur **déjà ouvert** renvoie la nouvelle fenêtre au bureau visible. Lancer avec un profil
  dédié, et `--force-renderer-accessibility` pour exposer le contenu de page à UIA :

```json
{"cmdline":"chrome.exe --new-window --force-renderer-accessibility --user-data-dir=\"%TEMP%\\agent\" \"https://example.com\""}
```

- `PrintWindow` peut renvoyer du noir sur certaines surfaces GPU/WebGL ; UIA (`/ui/*`) n'est pas
  affecté car il lit l'arbre de contrôles, pas les pixels.

## Limites connues

- `/batch`, `/key`, `/ui/scrape` passent encore par Windows-MCP (bureau **visible**).
- Apps bureautiques et natives classiques : pleinement supportées. Voir `README.md` pour le détail.
