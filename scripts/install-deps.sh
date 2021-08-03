#!/bin/bash

set -o errexit
set -o verbose

# Install local CDK CLI version
npm install

# Install project dependencies
pip-compile --upgrade requirements-dev.in
pip-compile --upgrade requirements.in
pip install -r requirements.txt -r requirements-dev.txt


#mypy --install-types
