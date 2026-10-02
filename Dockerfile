FROM python:3.12-slim

# Agents edit and run code, so nothing runs as root inside the container.
RUN useradd --create-home --uid 1000 agent

WORKDIR /app

# Install Python deps first so this layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The target repo the sub-agents will fix. COPIED IN (not bind-mounted) so the
# container is self-contained and agents can only touch their own copy.
# --chown gives the non-root user WRITE access to target_repo/ (all of it), and
# not to agent/ or orchestrator/.
#
# Each module lives in its own subdirectory. That layout is the whole basis for
# parallelism: one sub-agent's file tools are scoped to one subdirectory, so two
# agents using write_file at the same time can never touch the same file.
COPY --chown=agent:agent target_repo/ ./target_repo/

# The sub-agent (loop + tools + guards). Deliberately NOT chowned: it stays
# root-owned, so an agent running as `agent` cannot edit its own code or its
# own guards — only the target repo.
COPY agent/ ./agent/

# The orchestrator. Also root-owned: it decides who works on what, and no agent
# should be able to rewrite that.
COPY orchestrator/ ./orchestrator/

# Drop privileges.
USER agent

# Default command = the "verify" step: run the whole suite.
# Overridden later to run the orchestrator.
CMD ["python", "-m", "pytest", "-v", "target_repo/"]
