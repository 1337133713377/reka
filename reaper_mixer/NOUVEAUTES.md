Télécharge **REKA.exe** et lance-le. Il faut Voicemeeter (Potato ou Banana) et, pour les
effets, REAPER, FL Studio ou Equalizer APO. REKA se met ensuite à jour toute seule (pill
« Mise à jour » du Master). Pour voir la console sans rien installer : `REKA.exe --demo`.

## Prochaine version

- **Analyseur de spectre sur chaque strip** : le son de chaque entrée, après ses effets, des
  graves aux aigus, en barres qui retombent doucement. ⚙ → Interface → Spectre. Avec
  **REAPER**, un petit effet « REKA Spectrum » se place en dernier sur la piste de chaque strip
  (invisible dans REKA, le son passe tel quel) ; avec le moteur **REKA (intégré)**, c'est lui
  qui calcule. Il se met en pause quand la fenêtre est réduite.
- **Assistant de premiers réglages** : au premier lancement, six étapes pour choisir son
  micro (avec un VU-mètre pour vérifier qu'il capte), son casque, le nom des canaux, Discord
  et son style. Se relance depuis ⚙ → Général.
- **Éditeur de thèmes** : ⚙ → Apparence → *Créer un thème…*. Quatre couleurs (fond, panneaux,
  accent, texte) dont REKA tire toute la palette, la couleur des knobs, du fader, des VU…, le
  style des faders, avec un aperçu en direct et une alerte si l'accent ou le texte se lisent
  mal. Tes thèmes rejoignent « Mes thèmes » et s'échangent en fichiers `.rekatheme`.
- **Styles d'interface** : nouvel onglet ⚙ → Interface. Toute la console change de forme, pas
  seulement de couleur : **Cantabile** (gris plats, angles nets, canaux en lignes façon rack),
  **Lecteur 2000** (métal en relief, afficheurs LCD verts, boutons chromés, égaliseur animé dans
  le Master), **Minimal** (aplats sans bordure, faders en simple barre) ou **REKA** d'origine.
  Un aperçu réel de chaque style avant de l'appliquer.
- **Onze styles de plus** (15 en tout) : **Console analogique** (métal vissé, capuchons de
  couleur, VU-mètre à aiguille), **Rack 19"** (modules vissés, anneaux de LED, afficheurs
  rouges, un canal par unité), **Windows 11** (Mica, curseurs Fluent), **Tactile** (gros
  boutons et knobs pour écran tactile), **Terminal** (vert monospace, pointillés, balayage),
  **Plan d'architecte** (tout au trait blanc sur calque quadrillé), **Pixel 8-bit** (gros
  pixels sans lissage), **Verre** (panneaux translucides sur aurore), **Hi-fi 70s** (façade
  argentée, coffret en noyer, VU à aiguille), **Brutaliste** et **HUD sci-fi**.
- **Disposition** : densité compacte, normale ou aérée ; strips verticales ou **horizontales**
  (une ligne par canal, fader et VU couchés) ; animations activées, réduites ou désactivées.
- **Mode mixte** : garde la forme d'un style avec ton thème et tes couleurs par-dessus.
- **43 thèmes prédéfinis** (au lieu de 12), rangés par catégorie dans ⚙ → Apparence : Sobres,
  Néon & rétro, Chauds, Froids, Nature, Pastel, Éditeurs de code (Nord, Dracula, Gruvbox,
  Solarized, Tokyo Night, Catppuccin, Monokai, One Dark) et **Clairs** (Papier, Neige, Aube).
  Beaucoup ont leur propre fond, pas seulement une couleur d'accent. Filtre par catégorie et
  bouton « Thème au hasard ».
- **Égalisation du casque** : ⚙ → Écoute, tape le modèle de ton casque, et REKA corrige sa
  courbe avec le profil mesuré par AutoEQ (des milliers de casques et d'écouteurs). La courbe
  s'affiche ; un fichier `ParametricEQ.txt` ou une config Equalizer APO s'importent aussi.
- **Son surround virtuel** : chaque canal (jusqu'au 7.1) devient une enceinte placée autour de
  toi, dans la pièce de ton choix (aucune, salon, cinéma), avec la taille de tête et l'intensité
  réglables. Pastilles **EQ** et **3D** sous le knob Casque pour les allumer d'un clic. Marche
  avec tous les moteurs d'effets.
- **Moteur d'effets intégré, sans REAPER** (optionnel) : choisis *REKA (intégré)* dans
  ⚙ → Général → Effets. REKA charge lui-même tes plugins **VST2 et VST3** (64 bits) : ajout,
  bypass, interface, déplacement et copie avec les réglages. Il se branche directement dans
  Voicemeeter : pas de pilote ASIO à choisir, pas de tampon ajouté. Si un plugin plante, le
  moteur redémarre tout seul avec tes chaînes.
- **Chaînes d'effets dans le `.reka`** : avec le moteur intégré, l'export emporte chaque
  chaîne et les réglages de chaque plugin. À l'import, REKA retrouve les plugins installés
  ailleurs sur le disque, et garde les réglages de ceux qui manquent.

## 0.8.0

- **Rich Presence Discord** : ton profil affiche « Joue à REKA », deux lignes de texte que tu
  choisis, depuis combien de temps, et un bouton pour la télécharger. Désactivable dans
  ⚙ → Discord.
- **Micro synchronisé avec Discord** : coupe ton micro dans REKA, le micro barré s'affiche dans
  Discord, et l'inverse. Tu choisis le canal et le sens dans ⚙ → Discord. Il faut ta propre
  application Discord (2 minutes, expliqué dans l'onglet) : Discord réserve ce réglage au
  propriétaire de l'application.
- **Export / import de toute la config** : ⚙ → Général → *Sauvegarde de la config* écrit un
  seul fichier `.reka` (thème, couleurs, image d'arrière-plan, noms des canaux, routing, état
  de la console, projet REAPER avec ses chaînes d'effets). Pour changer de PC, réinstaller
  Windows ou partager tes réglages. Les chemins des moteurs et les secrets Discord n'y sont pas.

## 0.7.0

- **Entrées et sorties au choix** : ⚙ → Audio → *Entrées et sorties* règle le micro ou la carte
  son de chaque entrée, et le périphérique de chaque sortie (A1, A2…), en WDM, KS, MME ou
  ASIO, sans ouvrir Voicemeeter.
- **Charge CPU et GPU des effets** : deux jauges dans le Master montrent ce que consomment le
  moteur d'effets et tous ses plugins. Pour un plugin précis : clic droit sur l'effet →
  *Mesurer sa charge CPU*.
- **Effets déplaçables à la souris** : glisse un plugin plus haut ou plus bas dans sa chaîne,
  ou sur une autre strip ; il garde ses réglages. Ctrl en déposant = le copier.
- **Thèmes prédéfinis** : douze thèmes complets dans ⚙ → Apparence (Synthwave, Braise, Glace,
  Matrix, Or noir, Sakura, Océan, Rubis, Arcade, Monochrome…).

## 0.6.0

- Volume par application, musique en cours dans le Master, mises à jour automatiques,
  moteurs d'effets au choix (REAPER, FL Studio, Equalizer APO ou aucun), couleurs par élément.

REKA est développé par Regrettable et Zerka. Merci à 206 pour sa contribution.
