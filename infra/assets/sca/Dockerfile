FROM python:3.12-slim@sha256:646fb0bca3dd3ea1bcc6feb72c17ed16eed6e10cffc732fcc1478bd3e7f02d7b

RUN groupadd --system lineage \
    && useradd --system --gid lineage --create-home lineage
COPY apps/api/pyproject.toml apps/api/uv.lock /opt/lineage/apps/api/
RUN python -m pip install --no-cache-dir "uv==0.9.9" \
    && cd /opt/lineage/apps/api \
    && uv export --frozen --no-dev --extra aws --no-emit-project --output-file requirements.txt \
    && python -m pip install --no-cache-dir -r requirements.txt
COPY apps/api/src /opt/lineage/apps/api/src
RUN cd /opt/lineage/apps/api \
    && uv build --wheel --out-dir /opt/lineage/wheels \
    && python -m pip install --no-cache-dir --no-deps /opt/lineage/wheels/*.whl \
    && python -m pip check \
    && chown -R lineage:lineage /opt/lineage

USER lineage
CMD ["python", "-m", "lineage_api.entrypoints.sca_worker"]
