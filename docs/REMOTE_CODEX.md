# TruChat e Streamer autenticato: istruzioni per Codex remoto

## Obiettivo e riferimenti

Usare esclusivamente i fork personali:

- applicazione: `https://github.com/miki3421/truchat`, branch `advanced`;
- Streamer: `https://github.com/miki3421/openserverless-streamer`, branch `advanced`;
- sorgente dell'immagine: `a6344fe715a5605f0601da6a982abcc920b5cc8e`;
- tag immagine: `ghcr.io/miki3421/openserverless-streamer:advanced-a6344fe715a5605f0601da6a982abcc920b5cc8e`;
- digest immutabile: consultare `STREAMER_IMAGE.txt` in questa directory.

Non modificare repository Apache, non aprire PR e non fare push non richiesti.
Il lavoro autorizzato sul server riguarda TruChat e l'immagine del solo Streamer.
Non reinstallare OPS/K3s, non cancellare namespace, tenant, PVC, dati o cluster.
Non sostituire il catalogo task globale con un branch diverso per questa prova.
Procedere single agent. Le istruzioni autorizzano il deploy manuale sul target
concordato; se esiste un deploy automatico per questo repository, identificarlo
e scegliere un solo percorso per evitare due deploy concorrenti.

## 1. Preflight e conservazione dello stato

Identificare il server e il cluster effettivi prima di qualsiasi modifica:

```sh
hostname
whoami
ops -info
kubectl config current-context
kubectl get nodes -o wide
kubectl -n openserverless get statefulset openserverless-streamer-api
kubectl -n openserverless get pods -l app=openserverless-streamer-api -o wide
kubectl -n openserverless get statefulset openserverless-streamer-api \
  -o jsonpath='{range .spec.template.spec.containers[*]}{.name}{" "}{.image}{"\n"}{end}'
```

Usare il kubeconfig già autorizzato. Il contesto della CLI OPS e quello di
`kubectl` devono puntare allo stesso cluster. Non mostrare `.env`, `.wskprops`,
kubeconfig, Secret, parametri delle action o header Authorization nei report.
Se è un server di produzione non concordato, fermarsi dopo l'inventario e
ottenere l'autorizzazione per quello specifico target e la finestra di rollout.

L'immagine pubblicata supporta solo `linux/amd64`: controllare l'architettura
dei nodi. Se è ARM, occorre una build dedicata, non forzare questa immagine.
Verificare accesso in lettura al registry prima di cambiare il workload.
Se l'immagine richiede autenticazione, usare un imagePullSecret già approvato
oppure richiedere credenziali in lettura; non pubblicarle né riusare token di push.

Creare una directory privata per le prove e salvare: manifesto dello StatefulSet
Streamer, immagine precedente, strategia di aggiornamento, repliche e file del
catalogo che seleziona l'immagine. Conservare anche un checkout dell'app attuale
con le sue modifiche locali, dipendenze e artefatti web, senza copiarne segreti
nel fork o nei log condivisi. Non usare reset, clean o checkout forzati.

Inventariare **tutte** le applicazioni che usano lo Streamer condiviso: la
patch richiede handshake anche per loro. Il token protegge ciascuna richiesta
e non può essere disabilitato per compatibilità. Se altri produttori non sono
aggiornabili nella stessa finestra, fermarsi prima del cambio dell'immagine
condivisa e proporre un'istanza Streamer separata con routing esplicito.

## 2. Preparare e testare il fork dell'applicazione

Clonare in una nuova directory, senza sovrascrivere il progetto esistente:

```sh
git clone --branch advanced --single-branch https://github.com/miki3421/truchat.git
cd truchat
git rev-parse HEAD
git status --short
python3 -B -m unittest discover -s compatibility_tests -p 'test_*.py' -v
bun test compatibility_tests/chat-stream.test.ts
npm ci --ignore-scripts --no-audit --no-fund
npm run build
./node_modules/.bin/tsc -p tsconfig.app.json --noEmit
```

Se Bun non è nel PATH, usare quello incluso in OPS, adattando OS e architettura:
`~/.ops/linux-amd64/bin/bun`. I test Python richiedono solo la libreria standard.
Se Node.js/npm non sono presenti, usare un ambiente di build approvato; non
installare Docker nel cluster K3s soltanto per compilare il frontend.

Se Docker è già disponibile e autorizzato sul server, la prova opzionale è:

```sh
python3 -B compatibility_tests/smoke_streamer.py --image "$(cat docs/STREAMER_IMAGE.txt)"
```

Lo smoke test usa AI e controller simulati ma il vero proxy Streamer e il
metodo Python di TruChat. Non è un test completo del cluster né di sicurezza.

## 3. Aggiornare prima i client

Nel nuovo checkout usare il `.env` di TruChat tramite il meccanismo privato
già previsto sul server. Conservare i valori esistenti di `OPS_USER`,
`OPS_APIHOST`, `OPS_PASSWORD` e i binding AI già usati dall'app. Non inventare
credenziali o endpoint AI e non stampare i valori. `STREAM_TOKEN` **non** va
in `.env` e non è un nuovo parametro persistente dell'action.

Verificare che il profilo sia proprio quello del tenant TruChat atteso; poi,
se è stato scelto il percorso di deploy manuale:

```sh
ops ide login
ops ide deploy
```

Il manifest `package.json` contiene già la build del frontend per OPS. Controllare
che vengano aggiornati sia `v1/chat` sia i file web generati. Non modificare le
credenziali globali di un altro progetto. Il fork mantiene la compatibilità
con lo Streamer vecchio omettendo il preambolo quando il token non è fornito;
le nuove action emettono sempre i propri delimitatori JSON. I difetti del vecchio
proxy che inserisce newline ai confini TCP non vengono corretti dal solo client.

Aggiornare anche gli altri produttori Streamer identificati nel preflight e
verificare che inviino `OPS-STREAM <STREAM_TOKEN>\n` subito dopo la connessione,
prima di qualsiasi attesa per il primo token AI. Non inserire il preambolo nei
dati inviati al browser. Non accettare token vuoti o malformati.

## 4. Aggiornare il solo Streamer per digest

Solo dopo il controllo dei client, usare il riferimento immutabile nel file
`docs/STREAMER_IMAGE.txt`. I comandi seguenti assumono il namespace, StatefulSet
e container standard, verificati nel preflight:

```sh
kubectl -n openserverless set image statefulset/openserverless-streamer-api \
  openserverless-streamer-api="$(cat docs/STREAMER_IMAGE.txt)"
kubectl -n openserverless rollout status statefulset/openserverless-streamer-api --timeout=180s
kubectl -n openserverless get pods -l app=openserverless-streamer-api
kubectl -n openserverless get pod openserverless-streamer-api-0 \
  -o jsonpath='{range .status.containerStatuses[*]}{.name}{" "}{.imageID}{"\n"}{end}'
```

Controllare che la strategia permetta il rollout; se è `OnDelete`, non cancellare
pod automaticamente. Registrare anche il riferimento nella configurazione
effettiva che genera il workload: nei task 0.9.0 esaminati è
`opsroot.json`, campo `config.images.streamer`, esportato come `IMAGES_STREAMER`.
Verificare il percorso effettivo da `ops -info`, fare una copia privata e
modificare **solo quel valore**, preservando tutte le altre immagini e versioni.
Non fare `ops -update`, che potrebbe sovrascrivere il pin. Se un altro sistema
gestisce il manifesto, usare la sua sorgente invece di creare due configurazioni
in contrasto. Il solo `kubectl set image` non rende persistente il pin nei task.

Se il rollout fallisce, leggere stato/eventi del solo workload e ripristinare
l'immagine precedente; non reinstallare servizi o cambiare versioni per tentativi.

## 5. Accettazione sul server reale

Usare il normale URL di TruChat e provare nel browser:

1. benvenuto iniziale;
2. domanda breve con risposta progressiva;
3. output con accenti, emoji, newline e Markdown;
4. pulsanti RAG e Reset;
5. Stop durante la generazione e successiva nuova domanda;
6. due sessioni contemporanee, verificando che le risposte restino distinte;
7. verifica di un'altra applicazione Streamer aggiornata, se presente.

Controllare i codici HTTP e la risposta progressiva anche con una chiamata
alla route dell'app `/stream/web/v1/chat`, rispettando l'autenticazione già
configurata. Non disabilitarla per i test. Un `200` vuoto, JSON perso o risposta
troncata è un fallimento, anche se lo StatefulSet risulta Ready.

L'ingress standard riscrive `/stream/web/v1/chat` in
`/web/<tenant>/v1/chat`: conservare host e routing esistenti. Non esporre porte
callback, Kubernetes API, credenziali o servizi database all'esterno.

Annotare commit app, commit Streamer, digest installato, architettura, ingress,
risultati delle prove, eventuali riavvii ed errori. Non dichiarare completati
test che non sono stati eseguiti o scansioni di sicurezza non avviate.

## Limiti e finding ancora aperti

- 1 MiB di JSON per richiesta, inclusa la cronologia inviata dal browser.
- 20 secondi per aprire e autenticare il callback, inclusi scheduling/cold start.
- Un secondo per inviare il preambolo dopo l'accettazione TCP.
- 30 secondi senza dati callback: un modello lento può superare questo limite
  anche dopo il corretto handshake. La connessione viene chiusa.
- Cinque minuti complessivi. Il timeout di 10 minuti annotato sull'action non
  estende il proxy: provare una generazione lunga prima di dichiararla supportata.
- 64 stream globali per processo. Resta aperto il finding medio sulla saturazione
  da un singolo chiamante a danno degli altri tenant.
- Resta aperto il finding basso sugli handshake TCP parziali trattati in serie.

Non rimuovere limiti o autenticazione per far passare una prova. Se la latenza
reale del modello richiede limiti diversi, registrare i tempi e proporre una
modifica esplicita prima di implementarla. Questa pubblicazione non risolve i
due nuovi finding di disponibilità e non equivale a un'approvazione di sicurezza
per un ambiente di produzione multi-tenant.

## Rollback

In caso di regressione sostituire **solo** l'immagine del container Streamer
con l'esatto riferimento salvato nel preflight e attendere il rollout. Ripristinare
anche il corrispondente valore del catalogo, se modificato. Il client aggiornato
può restare con il server precedente: senza `STREAM_TOKEN` non invia il preambolo.
Se occorre ripristinare anche l'app, ripartire dal checkout/artefatto salvato,
senza perdere modifiche locali o credenziali. Non cancellare dati o volumi.
