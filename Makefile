.PHONY: install demo test api watch
install:
	pip install -r requirements.txt
demo:
	python -m demo
test:
	python -m pytest
api:
	uvicorn --factory task_agent.api:create_app --reload
watch:
	python -m task_agent watch
