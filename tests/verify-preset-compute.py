#!/usr/bin/env python3
"""Keep the preset verifier's FP4 floor and mixed-precision ordering aligned with serving."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("preset_weights", Path(__file__).with_name("verify-preset-weights.py"))
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class Checkpoint:
    def __init__(self, **documents):
        self.files = documents

    def read(self, name):
        return self.files[name]


def preset(floor="8.6"):
    return {"spec": {"requirements": {"minComputeCapability": floor}}}


def group(bits):
    return {"weights": {"type": "float", "num_bits": bits}}


class ComputeFloor(unittest.TestCase):
    def test_pure_fp4_formats_and_refusal_below(self):
        checkpoints = [
            Checkpoint(**{"config.json": {"quantization_config": {
                "quant_method": "compressed-tensors", "config_groups": {"fp4": group(4)}}}}),
            Checkpoint(**{"config.json": {"quantization_config": {
                "quant_method": "modelopt", "quant_algo": "NVFP4A16"}}}),
            Checkpoint(**{"params.json": {"quantization": {"qformat_weight": "nvfp4"}}}),
        ]
        for checkpoint in checkpoints:
            with self.subTest(checkpoint=checkpoint.files):
                self.assertEqual(verifier.hub_floor(checkpoint, preset())[0], "8.6")
                self.assertEqual(verifier.check_floor(checkpoint, "fp4", "org/fp4", preset())[0], "ok")
                self.assertEqual(verifier.check_floor(checkpoint, "fp4", "org/fp4", preset("8.0"))[0], "floor-understated")

    def test_fp8_wins_over_fp4_in_either_order(self):
        for bits in [(4, 8), (8, 4)]:
            checkpoints = [
                Checkpoint(**{"config.json": {"quantization_config": {
                    "quant_method": "compressed-tensors",
                    "config_groups": {str(bit): group(bit) for bit in bits}}}}),
                Checkpoint(**{
                    "config.json": {"quantization_config": {"quant_method": "modelopt"}},
                    "hf_quant_config.json": {"quantization": {"quantized_layers": {
                        str(bit): {"quant_algo": "NVFP4" if bit == 4 else "FP8"} for bit in bits}}}}),
            ]
            for checkpoint in checkpoints:
                with self.subTest(checkpoint=checkpoint.files):
                    floor, reason = verifier.hub_floor(checkpoint, preset())
                    self.assertEqual(floor, "8.9")
                    self.assertIn("FP8", reason)
                    self.assertEqual(verifier.check_floor(checkpoint, "mixed", "org/mixed", preset())[0], "floor-understated")

    def test_blackwell_recipe_keeps_its_floor(self):
        checkpoint = Checkpoint(**{"config.json": {"quantization_config": {
            "quant_method": "modelopt", "quant_algo": "NVFP4"}}})
        recipe = preset()
        recipe["spec"]["env"] = [{"name": "CUTE_DSL_ARCH", "value": "sm_120a"}]
        self.assertEqual(verifier.hub_floor(checkpoint, recipe)[0], "10.0")


if __name__ == "__main__":
    unittest.main()
