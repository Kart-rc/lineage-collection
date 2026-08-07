FROM public.ecr.aws/lambda/python:3.12@sha256:88a20996273ecb6fffece1d3bd52e5adc02336e3ac138f4af216d4076a904e0b

COPY apps/api/pyproject.toml apps/api/uv.lock /opt/lineage/apps/api/
RUN python -m pip install --no-cache-dir "uv==0.9.9" \
    && cd /opt/lineage/apps/api \
    && uv export --frozen --no-dev --extra aws --no-emit-project --output-file requirements.txt \
    && python -m pip install --no-cache-dir -r requirements.txt
COPY apps/api/src /opt/lineage/apps/api/src
RUN cd /opt/lineage/apps/api \
    && uv build --wheel --out-dir /opt/lineage/wheels \
    && python -m pip install --no-cache-dir --no-deps /opt/lineage/wheels/*.whl \
    && python -m pip check

CMD ["lineage_api.entrypoints.aws.intake.handler"]
