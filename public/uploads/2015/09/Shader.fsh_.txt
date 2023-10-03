precision mediump float;

uniform float time;
varying vec3 fragPosition;
varying vec4 fragColors;

#define TWO_PI (3.1459*2.0)
#define ITERATIONS 3.0

void main()
{
//    float progress = 0.5 * (sin(time + 0.5) + 1.0);
//    gl_FragColor = vec4((fragPosition.y + 1.0) / 2.0,
//                        (fragPosition.x + 1.0) / 2.0,
//                        (fragPosition.z + 1.0) / 2.0,
////                        (sin(time) + 1.0) / 2.0,
//                        1.0);


    gl_FragColor = vec4(fragColors); // vec4(fragColors.xyz,1.0);
}