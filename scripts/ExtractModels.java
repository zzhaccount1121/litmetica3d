import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;
import com.google.gson.*;
import net.minecraft.client.model.geom.*;
import net.minecraft.client.model.geom.builders.*;

public class ExtractModels {
    static Field cubes, children;
    static Object encode(ModelPart part) throws Exception {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("pose", new float[] {part.x,part.y,part.z,part.xRot,part.yRot,part.zRot,part.xScale,part.yScale,part.zScale});
        List<Object> boxes = new ArrayList<>();
        for (Object value : (List<?>)cubes.get(part)) {
            ModelPart.Cube cube = (ModelPart.Cube)value;
            List<Object> faces = new ArrayList<>();
            for (ModelPart.Polygon polygon : cube.polygons) {
                List<Object> vertices = new ArrayList<>();
                for (ModelPart.Vertex v : polygon.vertices())
                    vertices.add(new float[]{v.x(),v.y(),v.z(),v.u(),v.v()});
                faces.add(vertices);
            }
            boxes.add(faces);
        }
        result.put("cubes", boxes);
        Map<String,Object> descendants = new TreeMap<>();
        for (var entry : ((Map<String,ModelPart>)children.get(part)).entrySet())
            descendants.put(entry.getKey(), encode(entry.getValue()));
        result.put("children", descendants);
        return result;
    }
    public static void main(String[] args) throws Exception {
        net.minecraft.SharedConstants.tryDetectVersion();
        net.minecraft.server.Bootstrap.bootStrap();
        cubes = ModelPart.class.getDeclaredField("cubes"); cubes.setAccessible(true);
        children = ModelPart.class.getDeclaredField("children"); children.setAccessible(true);
        Map<String,Object> models = new TreeMap<>();
        for (var entry : LayerDefinitions.createRoots().entrySet())
            models.put(entry.getKey().toString(), encode(entry.getValue().bakeRoot()));
        Files.writeString(Path.of(args[0]), new GsonBuilder().serializeSpecialFloatingPointValues().create().toJson(models));
        System.out.println("EXTRACTED_LAYERS " + models.size());
        Map<String,Object> types = new TreeMap<>();
        for (var type : net.minecraft.core.registries.BuiltInRegistries.ENTITY_TYPE) {
            var size = type.getDimensions();
            types.put(net.minecraft.world.entity.EntityType.getKey(type).toString(), new float[]{size.width(), size.height()});
        }
        Files.writeString(Path.of(args[0]).resolveSibling("entity_types.json"), new Gson().toJson(types));
    }
}
