SHELL := bash
PYTHON_NAME = rhasspywake_snowboy_hermes
PACKAGE_NAME = rhasspy-wake-snowboy-hermes
SOURCE = $(PYTHON_NAME)
PYTHON_FILES = $(SOURCE)/*.py bin/*.py *.py
SHELL_FILES = bin/$(PACKAGE_NAME) debian/bin/* *.sh

.PHONY: reformat check dist venv test pyinstaller debian docker deploy

version := $(shell cat VERSION)
architecture := $(shell bash architecture.sh)

debian_package := $(PACKAGE_NAME)_$(version)_$(architecture)
debian_dir := debian/$(debian_package)

# -----------------------------------------------------------------------------
# Python
# -----------------------------------------------------------------------------

reformat:
	black .
	isort $(PYTHON_FILES)

check:
	flake8 $(PYTHON_FILES)
	pylint $(PYTHON_FILES)
	mypy $(PYTHON_FILES)
	black --check .
	isort --check-only $(PYTHON_FILES)
	bashate $(SHELL_FILES)
	yamllint .
	pip list --outdated

venv: snowboy-1.3.0.tar.gz
	rm -rf .venv/
	python3 -m venv .venv
	.venv/bin/pip3 install wheel setuptools
	.venv/bin/pip3 install -r requirements.txt
	.venv/bin/pip3 install snowboy-1.3.0.tar.gz
	.venv/bin/pip3 install -r requirements_dev.txt

dist: sdist debian

sdist:
	python3 setup.py sdist

test:
	bash etc/test/test_wavs.sh

# -----------------------------------------------------------------------------
# Docker
# -----------------------------------------------------------------------------

docker: pyinstaller
	docker build . -t "rhasspy/$(PACKAGE_NAME):$(version)" -t "rhasspy/$(PACKAGE_NAME):latest"

deploy:
	echo "$$DOCKER_PASSWORD" | docker login -u "$$DOCKER_USERNAME" --password-stdin
	docker push rhasspy/$(PACKAGE_NAME):$(version)

# -----------------------------------------------------------------------------
# Debian
# -----------------------------------------------------------------------------

pyinstaller: snowboy-1.3.0.tar.gz
	mkdir -p dist
	pyinstaller -y --workpath pyinstaller/build --distpath pyinstaller/dist $(PYTHON_NAME).spec
	mkdir -p pyinstaller/dist/$(PYTHON_NAME)/snowboy
	tar -C pyinstaller/dist/$(PYTHON_NAME)/snowboy -xvf snowboy-1.3.0.tar.gz --strip-components 1 snowboy-1.3.0/resources/common.res
	tar -C pyinstaller/dist -czf dist/$(PACKAGE_NAME)_$(version)_$(architecture).tar.gz $(SOURCE)/

debian: pyinstaller
	mkdir -p dist
	rm -rf "$(debian_dir)"
	mkdir -p "$(debian_dir)/DEBIAN" "$(debian_dir)/usr/bin" "$(debian_dir)/usr/lib"
	cat debian/DEBIAN/control | version=$(version) architecture=$(architecture) envsubst > "$(debian_dir)/DEBIAN/control"
	cp debian/bin/* "$(debian_dir)/usr/bin/"
	cp -R pyinstaller/dist/$(PYTHON_NAME) "$(debian_dir)/usr/lib/"
	cd debian/ && fakeroot dpkg --build "$(debian_package)"
	mv "debian/$(debian_package).deb" dist/

# -----------------------------------------------------------------------------
# Download
# -----------------------------------------------------------------------------

snowboy-1.3.0.tar.gz:
	curl -sSfL -o $@ 'https://github.com/Kitt-AI/snowboy/archive/v1.3.0.tar.gz'
