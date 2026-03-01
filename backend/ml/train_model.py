#!/usr/bin/env python3
"""
train_model.py – BotFC TensorFlow Action Classifier

Trains a small LSTM model on game_log.csv (real or simulated) to predict
the optimal FSM action (state) given a window of recent sensor readings.

The trained model is exported as a TFLite file that can be:
  1. Loaded on the laptop to suggest override actions to the operator
  2. Copied to the robot and loaded via tf.lite.Interpreter in future
     Python 3 versions of the brain

Architecture
────────────
  Input  : window of W timesteps × 12 sensor features
  LSTM   : 64 units → 32 units
  Dense  : 4 output classes (SEARCH | APPROACH | ALIGN | KICK)

Training loop
─────────────
  1. Load CSV (real + simulated)
  2. Normalise continuous features with MinMaxScaler
  3. Slide a window of W steps; label = state at last step
  4. 80/20 train/val split
  5. Train with early stopping
  6. Export: botfc_model.keras + botfc_model.tflite

Usage:
  pip install -r requirements.txt
  python3 train_model.py --csv game_log.csv sim_data.csv --window 20
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd

try:
    import tensorflow as tf
    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import LabelEncoder, MinMaxScaler
except ImportError as e:
    print("[train_model] Missing dependency:", e)
    print("  pip install -r requirements.txt")
    sys.exit(1)


# ── config ────────────────────────────────────────────────────────────────────

FEATURE_COLS = [
    "ball_valid", "ball_bx", "ball_by", "ball_bsz", "ball_dist",
    "ball_vx", "ball_vy", "ball_pred_bx", "ball_pred_by",
    "ball_confidence", "head_yaw", "inertial_roll",
]
LABEL_COL  = "state"
ALL_STATES = ["SEARCH", "APPROACH", "ALIGN", "KICK"]


# ── data loading ──────────────────────────────────────────────────────────────

def load_csvs(paths):
    frames = []
    for p in paths:
        if not os.path.exists(p):
            print("[train_model] WARNING: {} not found, skipping".format(p))
            continue
        df = pd.read_csv(p)
        # Keep only rows with known states
        df = df[df[LABEL_COL].isin(ALL_STATES)].reset_index(drop=True)
        frames.append(df)
    if not frames:
        raise RuntimeError("No valid CSV files loaded.")
    return pd.concat(frames, ignore_index=True)


def make_windows(df, window_size, scaler):
    X_list, y_list = [], []
    feats = scaler.transform(df[FEATURE_COLS].values.astype(float))
    labels = df[LABEL_COL].values

    for i in range(window_size, len(df)):
        X_list.append(feats[i - window_size:i])
        y_list.append(labels[i])

    return np.array(X_list, dtype=np.float32), np.array(y_list)


# ── model ─────────────────────────────────────────────────────────────────────

def build_model(window_size, n_features, n_classes):
    inp = tf.keras.Input(shape=(window_size, n_features))
    x = tf.keras.layers.LSTM(64, return_sequences=True)(inp)
    x = tf.keras.layers.Dropout(0.2)(x)
    x = tf.keras.layers.LSTM(32)(x)
    x = tf.keras.layers.Dropout(0.2)(x)
    x = tf.keras.layers.Dense(32, activation="relu")(x)
    out = tf.keras.layers.Dense(n_classes, activation="softmax")(x)
    model = tf.keras.Model(inputs=inp, outputs=out)
    model.compile(
        optimizer=tf.keras.optimizers.Adam(1e-3),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BotFC LSTM action classifier")
    parser.add_argument("--csv",     nargs="+", default=["game_log.csv"], help="Input CSV file(s)")
    parser.add_argument("--window",  type=int,  default=20,  help="Lookback window (timesteps)")
    parser.add_argument("--epochs",  type=int,  default=50,  help="Max training epochs")
    parser.add_argument("--batch",   type=int,  default=128, help="Batch size")
    parser.add_argument("--out-dir", default=".", help="Directory to save model artifacts")
    args = parser.parse_args()

    print("[train_model] Loading data from:", args.csv)
    df = load_csvs(args.csv)
    print("[train_model] Total rows: {:,}  |  State distribution:".format(len(df)))
    print(df[LABEL_COL].value_counts().to_string())

    # Encode labels
    le = LabelEncoder()
    le.fit(ALL_STATES)
    df["label"] = le.transform(df[LABEL_COL])

    # Fit scaler on all data (acceptable for unsupervised normalisation)
    scaler = MinMaxScaler()
    scaler.fit(df[FEATURE_COLS].values.astype(float))

    X, y_str = make_windows(df, args.window, scaler)
    y = le.transform(y_str)

    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, shuffle=True, random_state=42)
    print("[train_model] Train samples: {}  |  Val samples: {}".format(len(X_train), len(X_val)))

    n_classes  = len(ALL_STATES)
    n_features = len(FEATURE_COLS)
    model = build_model(args.window, n_features, n_classes)
    model.summary()

    os.makedirs(args.out_dir, exist_ok=True)
    keras_path  = os.path.join(args.out_dir, "botfc_model.keras")
    tflite_path = os.path.join(args.out_dir, "botfc_model.tflite")

    callbacks = [
        tf.keras.callbacks.EarlyStopping(monitor="val_accuracy", patience=8, restore_best_weights=True),
        tf.keras.callbacks.ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=4, min_lr=1e-5),
        tf.keras.callbacks.ModelCheckpoint(keras_path, monitor="val_accuracy", save_best_only=True),
    ]

    history = model.fit(
        X_train, y_train,
        validation_data=(X_val, y_val),
        epochs=args.epochs,
        batch_size=args.batch,
        callbacks=callbacks,
        verbose=1,
    )

    val_acc = max(history.history["val_accuracy"])
    print("[train_model] Best val accuracy: {:.2%}".format(val_acc))
    print("[train_model] Keras model saved to:", keras_path)

    # Export TFLite
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    tflite_model = converter.convert()
    with open(tflite_path, "wb") as f:
        f.write(tflite_model)
    print("[train_model] TFLite model saved to:", tflite_path)

    # Class mapping for inference
    mapping_path = os.path.join(args.out_dir, "class_mapping.txt")
    with open(mapping_path, "w") as f:
        for i, cls in enumerate(le.classes_):
            f.write("{}: {}\n".format(i, cls))
    print("[train_model] Class mapping saved to:", mapping_path)
    print("[train_model] Done.")


if __name__ == "__main__":
    main()
