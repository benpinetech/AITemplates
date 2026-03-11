// Emits 20ms frames of PCM16 (mono) via postMessage(ArrayBuffer)
class Pcm16Processor extends AudioWorkletProcessor {
    constructor() {
        super();
        this._buf = [];
        this._bufSamples = 0;
        this._samplesPerFrame = 48000 / 50; // 20ms @ 48k = 960 samples
    }

    process(inputs) {
        const input = inputs[0];
        if (!input || input.length === 0) return true;

        // mono: take channel 0
        const ch0 = input[0];
        if (!ch0) return true;

        // buffer, then flush in exact 960-sample frames
        for (let i = 0; i < ch0.length; i++) {
            this._buf.push(ch0[i]);
            this._bufSamples++;
            if (this._bufSamples >= this._samplesPerFrame) {
                const frame = this._buf.splice(0, this._samplesPerFrame);
                this._bufSamples -= this._samplesPerFrame;

                const ab = new ArrayBuffer(frame.length * 2);
                const view = new DataView(ab);
                for (let j = 0; j < frame.length; j++) {
                    let s = Math.max(-1, Math.min(1, frame[j]));
                    view.setInt16(j * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true); // little-endian
                }
                this.port.postMessage(ab, [ab]);
            }
        }
        return true;
    }
}

registerProcessor('pcm16-processor', Pcm16Processor);
