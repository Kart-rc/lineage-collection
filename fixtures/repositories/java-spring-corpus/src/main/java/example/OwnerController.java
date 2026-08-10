package example;

public class OwnerController {
    private final OwnerRepository owners;

    public OwnerController(OwnerRepository owners) {
        this.owners = owners;
    }

    public Owner find(Integer id) {
        return owners.findById(id).orElseThrow();
    }

    public Owner save(Owner owner) {
        return owners.save(owner);
    }
}
