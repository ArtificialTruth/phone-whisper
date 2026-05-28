#!/usr/bin/env python3
import os
import argparse
import torch
import torch.nn as nn
from transformers import Wav2Vec2ForCTC, Wav2Vec2CTCTokenizer
import onnx
from onnxruntime.quantization import QuantType, quantize_dynamic

class Wav2Vec2ONNXWrapper(nn.Module):
    def __init__(self, model, pad_token_id):
        super().__init__()
        self.model = model
        self.pad_token_id = pad_token_id

    def forward(self, x):
        """
        Args:
            x: (N, num_samples), float32
        Returns:
            logits: (N, num_frames, vocab_size), float32
        """
        # HF Wav2Vec2ForCTC expects input_values of shape (batch_size, sequence_length)
        outputs = self.model(x)
        logits = outputs.logits
        if self.pad_token_id != 0:
            num_classes = logits.shape[-1]
            indices = list(range(num_classes))
            indices[0] = self.pad_token_id
            indices[self.pad_token_id] = 0
            indices_tensor = torch.tensor(indices, dtype=torch.long, device=logits.device)
            logits = torch.index_select(logits, -1, indices_tensor)
        return logits

def add_meta_data(filename: str, meta_data: dict):
    """Add metadata props to the ONNX model in-place."""
    model = onnx.load(filename)
    while len(model.metadata_props):
        model.metadata_props.pop()

    for key, value in meta_data.items():
        meta = model.metadata_props.add()
        meta.key = key
        meta.value = str(value)

    onnx.save(model, filename)

def main():
    parser = argparse.ArgumentParser(description="Export Danish Wav2Vec2 to ONNX format.")
    parser.add_argument("--model-name", type=str, default="CoRal-project/roest-v3-wav2vec2-315m",
                        help="HuggingFace model ID or path to local model directory")
    parser.add_argument("--output-dir", type=str, default="roest-v3-wav2vec2-315m",
                        help="Directory to save the exported files")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    print(f"Loading tokenizer and model: {args.model_name}...")
    
    tokenizer = Wav2Vec2CTCTokenizer.from_pretrained(args.model_name)
    model = Wav2Vec2ForCTC.from_pretrained(args.model_name, torch_dtype=torch.float32)
    
    pad_token_id = tokenizer.pad_token_id
    if pad_token_id is None:
        pad_token_id = 0
    
    vocab_size = model.config.vocab_size
    print(f"Vocabulary size: {vocab_size}, pad_token_id: {pad_token_id}")

    # 1. Generate tokens.txt
    tokens_path = os.path.join(args.output_dir, "tokens.txt")
    vocab = tokenizer.get_vocab()
    # Sort by ID
    sorted_vocab = sorted(vocab.items(), key=lambda x: x[1])
    
    tokens_list = [token for token, index in sorted_vocab]
    if pad_token_id < len(tokens_list):
        t0 = tokens_list[0]
        tpad = tokens_list[pad_token_id]
        tokens_list[0] = tpad
        tokens_list[pad_token_id] = t0
    
    with open(tokens_path, "w", encoding="utf-8") as f:
        for index, token in enumerate(tokens_list):
            # Map the pipe separator '|' (common in Wav2Vec2) to the standard U+2581 block character 
            # so that the sherpa-onnx engine decodes it as a space ' '
            if token == "|":
                token = "\u2581"
            f.write(f"{token} {index}\n")
    print(f"Generated tokens.txt at {tokens_path}")

    # 2. Export Model to ONNX
    wrapper = Wav2Vec2ONNXWrapper(model, pad_token_id)
    wrapper.eval()
    
    # 10 seconds of 16kHz mono audio (160,000 samples)
    dummy_input = torch.randn(1, 16000 * 10)
    onnx_path = os.path.join(args.output_dir, "model.onnx")
    
    print(f"Exporting model to ONNX: {onnx_path}...")
    torch.onnx.export(
        wrapper,
        dummy_input,
        onnx_path,
        opset_version=14,
        input_names=["x"],
        output_names=["logits"],
        dynamic_axes={
            "x": {0: "N", 1: "num_samples"},
            "logits": {0: "N", 1: "num_frames"},
        },
    )

    # 3. Add Metadata
    meta_data = {
        "vocab_size": vocab_size,
        "model_type": "omnilingual-asr",
        "version": "1",
        "sample_rate": 16000,
        "model_author": "CoRal-project",
        "url": f"https://huggingface.co/{args.model_name}" if "/" in args.model_name else "",
        "comment": "danish-wav2vec2-315m-ctc",
    }
    print("Writing metadata props into ONNX model...")
    add_meta_data(onnx_path, meta_data)

    # 4. Quantize to INT8
    quant_path = os.path.join(args.output_dir, "model.int8.onnx")
    print(f"Quantizing ONNX model to INT8: {quant_path}...")
    quantize_dynamic(
        model_input=onnx_path,
        model_output=quant_path,
        op_types_to_quantize=["MatMul"],
        weight_type=QuantType.QUInt8,
    )
    print("Done! Export completed successfully.")
    print(f"Exported files location: {args.output_dir}/")

if __name__ == "__main__":
    main()
