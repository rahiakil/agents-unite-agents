PYTHONPATH := src
export PYTHONPATH

.PHONY: test dry-run

test:
	python3 -m unittest discover -s tests -v

dry-run:
	python3 -m harness wake --all-personas --dry-run --provider rule
