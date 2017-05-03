#!/bin/bash -eu

export PROJECT_VERSION_MAJOR=1
export PROJECT_VERSION_MINOR=3

if hash git 2>/dev/null; then
    export PROJECT_VERSION_PATCH=$(git rev-list --count HEAD)
else
    export PROJECT_VERSION_PATCH=0
fi

if [ "${CI_BUILD_REF_NAME:-dev}" == "release" ]; then
    DEV=""
else
    DEV="dev"
fi

export PROJECT_VERSION=${PROJECT_VERSION_MAJOR}.${PROJECT_VERSION_MINOR}.${DEV}${PROJECT_VERSION_PATCH}