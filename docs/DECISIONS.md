# Design decisions

This document explains *why* the system is built the way it is, in plain language. Each
section can be read on its own, without opening the code.

## 0. Repository and tooling

**One repo, several apps.** The backend, generator, benchmark and three frontends live
together so a single `docker compose up` can start everything and a single CI workflow can
test everything. Python projects share one `uv` workspace (one lock file, one set of dev
tools). The three frontends are independent npm projects, because Angular, Vite and Expo
each have their own build tooling and mixing them in one npm workspace tends to cause
dependency-hoisting problems (React Native is especially sensitive to that).

**Line endings.** The project is edited from Windows and WSL but built on Linux (Docker, CI).
`.gitattributes` forces LF so shell scripts and Dockerfiles don't break with `\r` characters.
