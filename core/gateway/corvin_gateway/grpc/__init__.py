"""gRPC surface for the Gateway.

ADR-0007 Phase 7.3. The proto file ``corvin.proto`` declares the
gRPC service that mirrors the REST surface. Phase 7 ships the
proto + a documented server skeleton (``grpc_server.py``); full
``grpcio`` integration is opt-in for operators who run gRPC.

To enable in production:

    pip install grpcio grpcio-tools
    python -m grpc_tools.protoc \\
      --python_out=. --grpc_python_out=. -I=. corvin.proto
    # Edit grpc_server.py to wire the generated stubs.

The REST surface in ``app.py`` stays authoritative; gRPC is a
parallel transport. Operators that don't enable gRPC see no
behaviour change.
"""
