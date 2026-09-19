DC = docker compose
BUILD = docker build
RUN = docker run -d
LOGS = docker logs
MIGRATION_CONTAINER = thm_cleanup_migration
ENV = --env-file .env

.PHONY: migration logs stop remove

migration:
	docker rm -f $(MIGRATION_CONTAINER) 2>/dev/null || true
	$(BUILD) --no-cache -t $(MIGRATION_CONTAINER) .
	$(RUN) --name $(MIGRATION_CONTAINER) $(ENV) $(MIGRATION_CONTAINER)

logs:
	$(LOGS) -f $(MIGRATION_CONTAINER)

stop:
	docker stop $(MIGRATION_CONTAINER)

remove:
	docker rm $(MIGRATION_CONTAINER)
	docker image rm $(MIGRATION_CONTAINER)