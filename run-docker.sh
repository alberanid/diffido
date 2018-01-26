#!/bin/sh

docker build -t davidealberani/diffido .
docker run --rm -it -v `pwd`/conf:/diffido/conf -v `pwd`/storage:/diffido/storage -p 3210:3210 davidealberani/diffido --debug

