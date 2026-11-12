Télécharge **REKA.exe** et lance-le. Il faut Voicemeeter (Potato ou Banana) et, pour les
effets, REAPER, FL Studio ou Equalizer APO. REKA se met ensuite à jour toute seule (pill
« Mise à jour » du Master). Pour voir la console sans rien installer : `REKA.exe --demo`.

## Prochaine version

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
