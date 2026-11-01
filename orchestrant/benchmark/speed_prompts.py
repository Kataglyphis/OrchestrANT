"""The speed lane's prompt set, short to long, so prefill and decode both show."""

SHORT_PROMPTS = [
    "What is 2+2?",
    "Say hello.",
    "What is the capital of France?",
    "Explain recursion in one sentence.",
    "What is an API?",
]

MEDIUM_PROMPTS = [
    "Write a Python function that computes the Fibonacci sequence using dynamic programming. Include a brief explanation of the time complexity.",
    "Explain the difference between REST and GraphQL APIs. When would you choose one over the other? Provide concrete examples.",
    "Write a bash script that monitors CPU and memory usage of a specific process every 5 seconds and logs the results to a CSV file.",
]

LONG_PROMPTS = [
    """Write a detailed technical blog post about building a production-ready LLM inference server. Cover the following aspects:
1. Model serving frameworks and their trade-offs (Ollama, vLLM, TGI, Triton Inference Server)
2. Hardware considerations: CPU vs GPU inference, memory requirements, quantization
3. API design: OpenAI-compatible endpoints, streaming, batching
4. Monitoring and observability: metrics to track, logging, alerting
5. Deployment strategies: Docker Compose, Kubernetes, auto-scaling
6. Security considerations: rate limiting, authentication, input sanitization

For each section, provide practical recommendations based on real-world production experience.""",
]
