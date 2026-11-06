.PHONY: data build test lint all
data:   ## download the UCI file and flatten it to CSV (about 2 minutes)
	python -m mart.cli data
build:  ## run every SQL layer, the checks, and write reports
	python -m mart.cli build
all:    ## build plus Excel workbook, explorer and charts
	python -m mart.cli all
test:
	python -m pytest -q
lint:
	python -m ruff check .
