# Lane on AWS

One Graviton EC2 runs the Docker stack. RDS holds the database, S3 the files, Secrets Manager the
secrets, Cognito the people. Everything is one CloudFormation stack, created and deleted with one
command, and a push to `main` deploys itself.

```
browser ─HTTPS─► Caddy ─┬─ /api/*  ─► api      ─┐   sign-in: Cognito managed login
                        ├─ /mail/* ─► mailpit   ├─► RDS Postgres 16 (private, SSL)
                        └─ /*      ─► web       │
                           worker ──────────────┼─► S3 (instance role, no keys)
                                                ├─► Secrets Manager (key, session, DB)
                                                └─► Anthropic API
                           logs ───────────────────► CloudWatch ─► alarms ─► your email
```

| Piece | Where |
|---|---|
| Web, API, worker, Mailpit, Caddy | EC2 `t4g.large`, Docker, `/opt/lane` |
| Database | RDS PostgreSQL 16, `db.t4g.micro`, private, encrypted, 7-day backups |
| Files | S3, public access blocked, encrypted |
| Secrets | Secrets Manager: `lane/app` (Anthropic key, session secret), `lane/db` (password) |
| Sign-in | Cognito user pool, invitation only, a group per role |
| Logs and alarms | CloudWatch Logs `/lane/<service>` (30 days); alarms emailed through SNS |
| Deploys | GitHub Actions with OIDC (no AWS keys in GitHub); images built on the instance |
| Domain | `laneunder.online` (Route 53 zone; registered at Porkbun), and `<ip>.sslip.io` kept |
| Certificates | Caddy, from Let's Encrypt, one per name |
| Real email | SES receives `submissions@laneunder.online` into S3 `inbound/`; the worker processes only authenticated, allowlisted senders whose virus and spam scans passed, and holds the rest |
| Admin access | SSM only; no SSH port is open |

Open to the internet: ports 80 and 443. Nothing else. The API answers only signed-in people
(health and sign-in excepted). Mailpit's inbox at `/mail` has its own password. Nothing secret
is in a file on the instance or on the Mac.

## Before the first deploy

- An AWS profile named `lane` (`aws sts get-caller-identity --profile lane` works).
- Docker Desktop running.
- `ANTHROPIC_API_KEY` in the repo's `.env` (it goes to Secrets Manager, nowhere else).

## First deploy

```bash
deploy/stack.sh up                      # asks once for the alarm email; ~15 min the first time
deploy/secrets.sh                       # the Anthropic key into Secrets Manager
deploy/gate.sh > deploy/gate.env        # the /mail password
deploy/deploy.sh                        # build, push, migrate, start, check
deploy/copy-cache.sh                    # the rehearsal's cache: the sample replays for $0
deploy/users.sh add you@example.com senior_underwriter "Your Name"
deploy/send-sample.sh                   # the agent's email arrives; the Brief fills in
```

Confirm the "AWS Notification - Subscription Confirmation" email, or alarms go nowhere.

Each person signs in at the site with the email and the temporary password Cognito sends them,
then chooses their own. Their role is their group.

```bash
deploy/users.sh add reviewer@example.com underwriter "Rita Reviewer"
deploy/users.sh role reviewer@example.com viewer
deploy/users.sh list
deploy/users.sh remove reviewer@example.com
```

After Mark as sent on the Brief, the agent's reply: `deploy/send-sample.sh reply`.

## Deploys

A push to `main` runs the checks (`ci`), then `deploy`: the commit is uploaded, built on the
instance (arm64, native), migrated and started; if the API or web is not healthy in five minutes,
the previous images start again and the job fails. The last three image tags are kept. Run it by
hand from the Actions tab (`deploy`, Run workflow). `deploy/deploy.sh` is the manual path from the
Mac, and the one that rewrites the instance's settings.

## Alarms (emailed)

| Alarm | When |
|---|---|
| `lane-site-down` | the health check on `/api/v1/health` fails 3 minutes running (and when it recovers) |
| `lane-host-recover` | AWS hardware under the instance fails: the instance is recovered automatically |
| `lane-host-reboot` | the instance stops answering its status check: it is rebooted automatically |
| `lane-run-failed` | a pipeline step or job fails (details in `/lane/worker`) |
| `lane-model-spend` | model calls cost more than $5 in a day |
| `lane-db-storage`, `lane-db-cpu` | the database has under 2 GB free, or runs hot for 15 minutes |

## Rehearse locally first

The same images and compose file, with Postgres standing in for RDS and the Mac's own MinIO
(`make minio`, bucket `lane-aws`) for S3. Do this before every first deploy: the AWS cache is
copied from this rehearsal, not from the Mac's database (macOS and Linux encode page images
differently, so the Mac's cache would miss on every PDF page).

```bash
cp deploy/env.aws.example deploy/.env    # then set the local values the comments name
docker build -f backend/Dockerfile -t lane-api:local .
docker build -f frontend/Dockerfile --build-arg NEXT_PUBLIC_LANE_API_URL=http://localhost:8080 -t lane-web:local .
cd deploy
docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm migrate
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d
./send-sample.sh local
```

Open http://localhost:8080.

## Operate

```bash
deploy/stack.sh outputs                                   # the site, instance, database host
aws ssm start-session --target <InstanceId> --profile lane # a shell on the instance
```

On the instance, in `/opt/lane`: `docker compose ps`, `docker compose logs -f worker`,
`docker compose restart api`. Logs rotate at 10 MB, three files per service. Every container
restarts on its own after a crash or a reboot.

## Tear down

```bash
deploy/stack.sh down
```

It asks you to type the stack name, empties the bucket and deletes the stack: instance, address,
database (without a final snapshot), images and roles. Nothing billable is left.

## Cost

About $68 a month: EC2 about $49, RDS about $12, the address and storage, two secrets ($0.80),
the health check and alarms (about $1.30), logs (cents). Cognito is free at this size. Model calls
are extra and only for new input; a replay of the sample is $0.

## Why it is built this way

- **The API address is fixed in the web build.** Next.js bakes `NEXT_PUBLIC_LANE_API_URL` in, so
  `deploy.sh` builds the web image for the site's own address, and Caddy serves the API on the same
  origin. No CORS, no mixed content.
- **One Mailpit watcher.** The worker watches the inbox and runs the pipeline. A second watcher
  would race it for every email.
- **Sign-in is Lane's own, backed by Cognito.** The browser signs in on Cognito's page (PKCE, no
  client secret) and the API exchanges the verified ID token for Lane's session, so permissions,
  decision authority and the audit's names work exactly as with the demo people.
- **Secrets are read at start.** The API and worker read `lane/app` and `lane/db` with the
  instance role; the settings file on the instance holds no secret. The database password is
  generated once and never rotated, so the app's copy never goes stale.
- **Production refuses the development secret.** `LANE_ENV=production` makes the API refuse to
  start with the default session secret, and anonymous requests are refused.
- **The gate's Basic header never reaches the API.** Once the password is accepted, browsers
  resend it on page images and live events, which carry the sign-in token in the URL; Caddy
  drops it so the API reads the token.
- **The OCR models are in the image.** RapidOCR would otherwise download them the first time a
  scan is read.
- **IMDSv2 with a hop limit of 2,** so the containers can use the instance role for S3.

## Real email

Agents email `submissions@laneunder.online`. SES scans it (spam, virus) and stores it in
`s3://<bucket>/inbound/`; the worker reads it with the same poller as Mailpit
(`lane/intake/ses.py`) and processes it only when the virus and spam verdicts passed, SES
authenticated the sender (DMARC, or DKIM for the From domain) and the sender is on the allowlist
(`LANE_ALLOWED_SENDERS` in `deploy/stack.env`, plus `agent@laneunder.online`). Anything else goes
to `inbound/held/`, logged as `intake.held`, never processed.

```bash
deploy/send-sample.sh --ses          # the sample packet, as Daniel Okafor <agent@laneunder.online>
deploy/send-sample.sh --ses reply    # the agent's reply, after Mark as sent
```

## Next

ECS Fargate and a load balancer if it has to carry real load (docs/architecture has the
production design). The images do not change.
