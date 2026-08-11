from __future__ import annotations


def main() -> None:
    try:
        from lineage_api.infrastructure.aws.composition import run_sca_worker
    except ModuleNotFoundError as error:
        raise RuntimeError("AWS SCA worker composition is not installed") from error
    run_sca_worker()


if __name__ == "__main__":
    main()
