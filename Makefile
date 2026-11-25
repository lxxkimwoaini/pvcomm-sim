.PHONY: bootstrap lint format typecheck test schemas web-test web-build dev-web test-lab check
bootstrap lint format typecheck test schemas web-test web-build dev-web test-lab check:
	uv run --frozen python scripts/dev.py $@
