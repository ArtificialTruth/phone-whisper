#!/bin/bash
set -e

MODEL_PATH=$1

if [ -z "$MODEL_PATH" ]; then
    echo "Usage: ./scripts/push_model.sh <path/to/model-dir>"
    exit 1
fi

if [ -z "$ANDROID_HOME" ]; then
    ANDROID_HOME="$HOME/Android/Sdk"
fi

ADB="$ANDROID_HOME/platform-tools/adb"

if [ ! -f "$ADB" ]; then
    if command -v adb &> /dev/null; then
        ADB=$(command -v adb)
    else
        echo "Error: adb not found. Please set ANDROID_HOME or ensure adb is in PATH."
        exit 1
    fi
fi

MODEL_NAME=$(basename "$MODEL_PATH")

echo "Creating temporary directory on device..."
"$ADB" shell "mkdir -p /data/local/tmp/$MODEL_NAME"

echo "Creating destination directory in application's files..."
"$ADB" shell "run-as com.kafkasl.phonewhisper mkdir -p files/models/$MODEL_NAME"

# Check if an int8 quantized version exists to skip large unquantized assets
HAS_INT8=false
if [ -f "$MODEL_PATH/model.int8.onnx" ]; then
    HAS_INT8=true
fi

echo "Pushing and copying files to app files directory..."
for f in "$MODEL_PATH"/*.onnx "$MODEL_PATH"/*.ort "$MODEL_PATH"/*.txt "$MODEL_PATH"/*.data; do
    if [ -f "$f" ]; then
        fname=$(basename "$f")
        
        # Skip unquantized model assets if we have the int8 quantized one
        if [ "$HAS_INT8" = true ] && { [ "$fname" = "model.onnx" ] || [ "$fname" = "model.onnx.data" ]; }; then
            echo "  skipping unquantized $fname (model.int8.onnx is present)..."
            continue
        fi
        
        echo "  processing $fname..."
        "$ADB" push "$f" "/data/local/tmp/$MODEL_NAME/$fname" > /dev/null
        "$ADB" shell "run-as com.kafkasl.phonewhisper cp /data/local/tmp/$MODEL_NAME/$fname files/models/$MODEL_NAME/$fname"
    fi
done

echo "Cleaning up temporary files on device..."
"$ADB" shell "rm -rf /data/local/tmp/$MODEL_NAME"

echo "Successfully pushed model: $MODEL_NAME"
