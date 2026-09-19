FROM docker.io/library/ubuntu:24.04

ARG USERNAME=dev
ARG UID=1000
ARG GID=1000

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 ca-certificates

COPY boxman /opt/boxman/boxman
ENV PYTHONPATH=/opt/boxman

RUN python3 -m boxman.cli system --packages-only \
    && rm -rf /var/lib/apt/lists/*

RUN if getent passwd "${UID}" >/dev/null; then \
        userdel --remove "$(getent passwd "${UID}" | cut -d: -f1)"; \
    fi \
    && if getent group "${GID}" >/dev/null; then \
        groupmod --new-name "${USERNAME}" "$(getent group "${GID}" | cut -d: -f1)"; \
    else \
        groupadd --gid "${GID}" "${USERNAME}"; \
    fi \
    && useradd --uid "${UID}" --gid "${GID}" -m -s /bin/bash "${USERNAME}"

USER ${USERNAME}
WORKDIR /home/${USERNAME}
RUN python3 -m boxman.cli user

ENV PATH="/home/${USERNAME}/.local/bin:/home/${USERNAME}/.local/share/node-v22/bin:${PATH}"
CMD ["/bin/bash"]
