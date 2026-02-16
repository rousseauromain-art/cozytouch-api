Voici le contenu d'un README.md exhaustif. Tu peux leur partager ce texte ou le mettre sur un repo privé.

🌀 CozyControl-Bot : Pilotage Thermique & Analyse de Data
Ce projet est un middleware asynchrone permettant de piloter des radiateurs Atlantic Oniris (IO Homecontrol) et d'analyser leur précision thermique via des sondes Shelly.

🛠️ Le Défi du Reverse Engineering (Overkiz API)
Le point le plus complexe a été de comprendre la machine à états des radiateurs Atlantic. Contrairement à des objets connectés simples, un radiateur Oniris possède des dizaines de "states" et de "commands" cachées.

Comment nous avons trouvé les commandes :
Nous avons utilisé le Dump de Debug de l'objet Device via pyoverkiz. En inspectant les definition.commands et les states en temps réel, nous avons découvert :

La séparation des widgets : Le radiateur est un HeatingElement, mais le sèche-serviette est un TowelDryer. Ils ne répondent pas aux mêmes appels système.

Le conflit de Mode : Envoyer une consigne (setTargetTemperature) ne suffit pas si le radiateur est en mode auto. Il faut forcer un mode "dérogatoire" (appelé basic ou external selon le widget) pour que la température soit appliquée immédiatement.

Le multiplexage des composants : Chaque radiateur est une URL type io://xxxx/yyyy#1. Le #1 est le composant de chauffe, mais les sondes peuvent être sur le #0. Notre script itère sur les composants pour agréger la donnée.

# 🌀 CozyControl-Bot : Pilotage Thermique & Analyse de Data (v9.22)

Ce projet est un orchestrateur Python asynchrone conçu pour piloter des radiateurs **Atlantic Oniris (IO Homecontrol)** via l'API **Cozytouch (Overkiz)** et monitorer la précision thermique via des sondes **Shelly Cloud**.

## 🏗️ Architecture & Flux de Données

Le système est déployé sur **Koyeb** (Micro-services) et repose sur une boucle d'événements `asyncio`.


### 1. Stack Technique
* **Runtime :** Python 3.10+
* **Interface :** `python-telegram-bot` (Polling)
* **Persistence :** PostgreSQL (Historique des températures)
* **Clients API :** * `pyoverkiz` (Reverse-engineering du protocole IO Homecontrol)
    * `httpx` (Consommation API REST Shelly)

---

## 🛠️ Le Défi du Reverse Engineering (Focus Atlantic)

L'un des points majeurs du projet a été le "dumping" des capacités des appareils Atlantic pour comprendre leurs machines à états. Contrairement à des thermostats ON/OFF, les Oniris et Sèche-serviettes possèdent des registres de commandes spécifiques.

### A. Identification des Commandes via Debug
Grâce à l'introspection d'objets `pyoverkiz`, nous avons extrait les commandes atomiques :

| Type Équipement | Commande de Mode | Valeur Maison (Auto) | Valeur Absence (Manu) |
| :--- | :--- | :--- | :--- |
| **Radiateur Oniris** | `setOperatingMode` | `internal` | `basic` |
| **Sèche-Serviette** | `setTowelDryerOperatingMode` | `internal` | `external` |

### B. Injection des Consignes (Setpoint)
Le pilotage utilise la commande `setTargetTemperature`. 
* **Atomicité :** Pour éviter les désynchronisations, le script utilise `execute_commands(url, [Command1, Command2])`. Cela garantit que la consigne et le changement de mode sont traités dans la même transaction par le bridge Cozytouch.
* **Typage :** L'API Overkiz est sensible au typage ; nous forçons des `float` (ex: `16.0` et non `16`) pour éviter les erreurs `400 Bad Request`.

---

## 📊 Monitoring & Data Logging

Le script ne se contente pas d'exécuter des ordres, il agit comme un **Data Logger** :

1. **Background Worker :** Une tâche `asyncio` tourne en 24/7 et effectue un snapshot horaire.
2. **Normalisation :** Il agrège les données de la sonde interne Atlantic (souvent biaisée car proche du corps de chauffe) et de la sonde de référence Shelly (placée au centre du bureau).
3. **Analyse SQL :** Le rapport "Stats 7J" exécute une agrégation pour calculer le **Delta moyen**.
   ```sql
   SELECT AVG(temp_shelly - temp_radiateur) FROM temp_logs 
   WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days';

   
🏗️ Architecture & Flux de Données
Le système repose sur une boucle d'événements asyncio tournant sur Koyeb.

1. Ingestion & Persistence (Koyeb ↔ PostgreSQL)
Le script ne se contente pas de piloter ; il historise.

Le Background Worker : Un thread asynchrone background_logger tourne en 24/7.

Le Job Horaire : Chaque heure, il fait un "Snapshot" de l'installation. Il interroge simultanément le Cloud Cozytouch et le Cloud Shelly (via requête POST signée).

Normalisation : Les données hétérogènes sont normalisées et injectées dans PostgreSQL pour permettre des requêtes SQL complexes sur l'inertie thermique.

2. Le Pipeline d'Exécution Telegram
Quand un utilisateur clique sur une option, le flux est le suivant :

Trigger : CallbackQueryHandler reçoit l'interaction.

Atomicité : On utilise client.execute_commands(url, [cmd1, cmd2]). Envoyer les deux commandes dans une seule liste est crucial pour que l'API Overkiz les traite comme une transaction unique, évitant ainsi que le radiateur ne reprenne sa consigne précédente entre deux appels.

UI Update : Le bot édite son propre message pour afficher un rapport d'exécution granulaire (appareil par appareil).

📈 Monitoring du Différentiel (Le "Delta")
L'un des intérêts majeurs pour un dev est le calcul du Delta de précision.
Les radiateurs Oniris ont tendance à auto-estimer leur température près du corps de chauffe. En croisant ces données avec un capteur Shelly placé au centre de la pièce (Bureau), le script calcule en SQL le décalage moyen sur 7 jours.

Cela permet d'ajuster les consignes de confort de manière logicielle (ex: demander 19.5°C pour obtenir un 19°C réel).

🚀 Déploiement sur Koyeb
Le déploiement est géré via un Dockerfile (ou buildpack Python) avec les variables d'environnement suivantes :

TELEGRAM_TOKEN : Auth BotFather.

OVERKIZ_EMAIL/PASS : Credentials Cozytouch.

DATABASE_URL : Connection string PostgreSQL.

SHELLY_TOKEN/ID : Auth Cloud Shelly.

Le port 8000 est exposé pour le Health Check TCP/HTTP de Koyeb, garantissant que l'instance est redémarrée automatiquement en cas de crash de la boucle asyncio.

C'est un beau projet d'intégration d'APIs tierces ! Est-ce que tu veux que je te prépare le fichier requirements.txt qui va avec pour qu'ils aient la liste complète des dépendances ?
