"""Pass-through pipeline: copies the input run's `signal` array to a new run.

Tiny enough that the notebook fits in one cell of real logic. Designed
for end-to-end smoke tests of the pipelines wiring, not as a useful
data product.
"""
from lucid_pipelines.plugin import PipelinePlugin


class PassthroughPipeline(PipelinePlugin):
    name = "passthrough"
    description = "Copy input/signal array verbatim to a new run (test fixture)"
    display_name = "Passthrough (test)"
    parameters_schema = {
        "stream": {
            "type": "string",
            "default": "primary",
            "description": "Tiled child name carrying the input array",
        },
        "field": {
            "type": "string",
            "default": "signal",
            "description": "Array key under the stream",
        },
    }
    output_tags = ["e2e-test"]
    notebook = "passthrough.ipynb"
    package_name = "passthrough_pipeline"
    inherit_input_access_blob = True
    store_executed_notebook = False
    timeout_seconds = 120
