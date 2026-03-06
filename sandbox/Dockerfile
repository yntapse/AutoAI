FROM python:3.11-slim

# Create non-root user
RUN useradd -m sandboxuser

WORKDIR /app

# Install core ML libraries
RUN pip install --no-cache-dir \
    numpy \
    pandas \
    scikit-learn \
    xgboost

# Switch to non-root user
USER sandboxuser

ENTRYPOINT ["python"]
