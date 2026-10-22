// Reader for the API's binary payloads (apps/api/src/atelier_api/binary.py, ADR-028):
// uint32 LE header length, JSON header (4-byte padded), then 4-byte aligned arrays.

export type TypedArray = Float32Array | Uint8Array | Int32Array | Float64Array;

export interface ArraySpec {
  name: string;
  dtype: string;
  shape: number[];
}

export interface Payload<H> {
  header: H & { arrays: ArraySpec[] };
  arrays: Record<string, TypedArray>;
}

const DTYPES: Record<string, { size: number; make: (b: ArrayBuffer, o: number, n: number) => TypedArray }> = {
  "<f4": { size: 4, make: (b, o, n) => new Float32Array(b, o, n) },
  "<f8": { size: 8, make: (b, o, n) => new Float64Array(b, o, n) },
  "<i4": { size: 4, make: (b, o, n) => new Int32Array(b, o, n) },
  "|u1": { size: 1, make: (b, o, n) => new Uint8Array(b, o, n) },
};

export function unpack<H>(buffer: ArrayBuffer): Payload<H> {
  const view = new DataView(buffer);
  const length = view.getUint32(0, true);
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, length)));
  let offset = 4 + length;
  const arrays: Record<string, TypedArray> = {};
  for (const spec of header.arrays as ArraySpec[]) {
    const kind = DTYPES[spec.dtype];
    if (!kind) throw new Error(`unsupported dtype ${spec.dtype}`);
    const count = spec.shape.reduce((a, b) => a * b, 1);
    if (offset % kind.size !== 0) throw new Error("misaligned array");
    arrays[spec.name] = kind.make(buffer, offset, count);
    offset += count * kind.size;
    offset += (4 - (offset % 4)) % 4;
  }
  return { header, arrays };
}
