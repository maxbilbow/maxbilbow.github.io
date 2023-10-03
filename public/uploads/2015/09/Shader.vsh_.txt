precision mediump float;

attribute vec4 aPosition;
attribute vec4 colors;
uniform float time;
uniform mat4 u_matrix;
varying vec3 fragPosition;
varying vec4 fragColors;

uniform float fov;
uniform float aspect;
uniform float near;
uniform float far;

#define PI (3.1459)
#define TWO_PI (3.1459*2.0)
void main()
{
    float f = tan(PI * 0.5 - 0.5 * 45.0);
    float rangeInv = 1.0 / (near - far);
    float r = time;//-0.8;
    mat4 proj = mat4(
                  f / aspect, 0.0, 0.0, -0.0,
                  0.0, f, 0.0, 0.0,
                  0.0, 0.0, (near + far) * rangeInv, -1.0,
                  0.0, 0.0, near * far * rangeInv * 2.0,  0.0
                  );
    
    mat4 ry = mat4(
        cos(r), 0.0, sin(r), -0.0,
        0.0, 1.0, 0.0, -0.0,
        -sin(r), 0.0, cos(r), -0.0,
        0.0, 0.0, 0.0,  1.0
    );
    
    mat4 rx = mat4(
                   1.0, 0.0, 0.0, -0.0,
                   0.0, cos(r), -sin(r), -0.0,
                   0.0, sin(r), cos(r), -0.0,
                   0.0, 0.0, 0.0,  1.0
                   );
    
    mat4 translation = mat4(
                           1.0, 0.0, 0.0, 0.0,
                            0.0, 1.0, 0.0, 0.0,
                           0.0, 0.0, 1.0, 0.0,
                            0.0, 0.0, -4.0,  1.0
                            );
    gl_Position = proj * translation  * ry * aPosition;// vec4(aPosition.x, aPosition.y, aPosition.z-0.5, 1.0);
//    vec4(progress * aPosition.x + (1.0 - progress) * aPosition.y,
//                       progress * aPosition.y + (1.0 - progress) * aPosition.x,
//                       0.0,1.0);
    fragPosition = gl_Position.xyz;
    fragColors = vec4(colors);
}
