# Memory deployment example

This example shows the observed service boundaries without publishing production credentials or custom Memory Service source.

Run PostgreSQL and Hindsight with Docker Compose. Run the dedicated LLM and embedding llama-server units on the host. Run the custom Memory Service as a loopback system service after Docker is available.

The Compose file references Hindsight 0.8.6 and a PostgreSQL 18 VectorChord suite image because those were observed. Pin immutable digests in a real deployment after checking current upstream release and license information.

Create a private .env with database credentials and the Hindsight database URL. Do not commit it.
