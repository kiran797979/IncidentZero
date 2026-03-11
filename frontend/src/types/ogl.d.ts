declare module 'ogl' {
  export class Renderer {
    constructor(options?: { dpr?: number; depth?: boolean; alpha?: boolean });
    gl: any;
    setSize(width: number, height: number): void;
    render(options: { scene: any; camera: any }): void;
  }
  export class Camera {
    constructor(gl: any, options?: { fov?: number });
    position: { set(x: number, y: number, z: number): void };
    perspective(options: { aspect: number }): void;
  }
  export class Geometry {
    constructor(gl: any, attributes: Record<string, { size: number; data: Float32Array }>);
  }
  export class Program {
    constructor(gl: any, options: {
      vertex: string;
      fragment: string;
      uniforms: Record<string, { value: any }>;
      transparent?: boolean;
      depthTest?: boolean;
    });
    uniforms: Record<string, { value: any }>;
  }
  export class Mesh {
    constructor(gl: any, options: { mode: number; geometry: Geometry; program: Program });
    position: { x: number; y: number; z: number };
    rotation: { x: number; y: number; z: number };
  }
}