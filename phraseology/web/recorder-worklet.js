// Passes microphone frames to the page. The page keeps a short pre-roll
// so the first syllable after pressing the key is not lost.
class Recorder extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch && ch.length) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("recorder", Recorder);
